# -*- coding: utf-8 -*-
"""Anomaly Detection with Generative Adversarial Networks  (AnoGAN)
 Paper: https://arxiv.org/pdf/1703.05921.pdf
 Note, that this is another implementation of AnoGAN as the one from https://github.com/fuchami/ANOGAN
"""
# Author: Michiel Bongaerts (but not author of the AnoGAN method)
# License: BSD 2 clause


import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.utils import check_array
from sklearn.utils.validation import check_is_fitted

from .base import BaseDetector
from .base_dl import _get_tensorflow_version
from ..utils.utility import check_parameter

# if tensorflow 2, import from tf directly
if _get_tensorflow_version() == 1:
    raise NotImplementedError('Model not implemented for Tensorflow version 1')

else:
    import tensorflow as tf
    from tensorflow.keras.models import Model
    from tensorflow.keras.layers import (Input, Dense, Dropout)
    from tensorflow.keras.optimizers import Adam


class AnoGAN(BaseDetector):
    """Anomaly Detection with Generative Adversarial Networks  (AnoGAN). See the original paper
    "Unsupervised anomaly detection with generative adversarial networks to guide marker discovery"

    See :cite:`schlegl2017unsupervised` for details.

    Parameters
    ----------

    output_activation : str, optional (default=None)
        Activation function to use for output layer.
        See https://keras.io/activations/


    activation_hidden : str, optional (default='tanh')
        Activation function to use for output layer.
        See https://keras.io/activations/

    epochs : int, optional (default=500)
        Number of epochs to train the model.

    batch_size : int, optional (default=32)
        Number of samples per gradient update.

    dropout_rate : float in (0., 1), optional (default=0.2)
        The dropout to be used across all layers.

    G_layers : list, optional (default=[20,10,3,10,20])
        List that indicates the number of nodes per hidden layer for the generator.
        Thus, [10,10] indicates 2 hidden layers having each 10 nodes.

    D_layers : list, optional (default=[20,10,5])
        List that indicates the number of nodes per hidden layer for the discriminator.
        Thus, [10,10] indicates 2 hidden layers having each 10 nodes.


    learning_rate: float in (0., 1), optional (default=0.001)
        learning rate of training the the network

    index_D_layer_for_recon_error: int, optional (default = 1)
        This is the index of the hidden layer in the discriminator for which the reconstruction error 
        will be determined between query sample and the sample created from the latent space.

    learning_rate_query: float in (0., 1), optional (default=0.001)
        learning rate for the backpropagation steps needed to find a point in the latent space
        of the generator that approximate the query sample


    epochs_query: int, optional (default=20) 
        Number of epochs to approximate the query sample in the latent space of the generator

    preprocessing : bool, optional (default=True)
        If True, apply standardization on the data.

    verbose : int, optional (default=1)
        Verbosity mode.
        - 0 = silent
        - 1 = progress bar

    contamination : float in (0., 0.5), optional (default=0.1)
        The amount of contamination of the data set, i.e.
        the proportion of outliers in the data set. When fitting this is used
        to define the threshold on the decision function.

    Attributes
    ----------

    decision_scores_ : numpy array of shape (n_samples,)
        The outlier scores of the training data [0,1].
        The higher, the more abnormal. Outliers tend to have higher
        scores. This value is available once the detector is
        fitted.

    threshold_ : float
        The threshold is based on ``contamination``. It is the
        ``n_samples * contamination`` most abnormal samples in
        ``decision_scores_``. The threshold is calculated for generating
        binary outlier labels.

    labels_ : int, either 0 or 1
        The binary labels of the training data. 0 stands for inliers
        and 1 for outliers/anomalies. It is generated by applying
        ``threshold_`` on ``decision_scores_``.
    """

    def __init__(self, activation_hidden='tanh', dropout_rate=0.2,
                 latent_dim_G=2,
                 G_layers=[20, 10, 3, 10, 20], verbose=0,
                 D_layers=[20, 10, 5], index_D_layer_for_recon_error=1,
                 epochs=500,
                 preprocessing=False, learning_rate=0.001, learning_rate_query=0.01,
                 epochs_query=20,
                 batch_size=32, output_activation=None, contamination=0.1):
        super(AnoGAN, self).__init__(contamination=contamination)

        self.activation_hidden = activation_hidden
        self.dropout_rate = dropout_rate
        self.latent_dim_G = latent_dim_G
        self.G_layers = G_layers
        self.D_layers = D_layers
        self.index_D_layer_for_recon_error = index_D_layer_for_recon_error
        self.output_activation = output_activation
        self.contamination = contamination
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.learning_rate_query = learning_rate_query
        self.epochs_query = epochs_query
        self.preprocessing = preprocessing
        self.batch_size = batch_size
        self.verbose = verbose

        check_parameter(dropout_rate, 0, 1, param_name='dropout_rate', include_left=True)

    def _build_model(self):
        #### Generator #####
        G_in = Input(shape=(self.latent_dim_G,), name='I1')
        G_1 = Dropout(self.dropout_rate, input_shape=(self.n_features_,))(G_in)
        last_layer = G_1

        G_hl_dict = {}
        for i, l_dim in enumerate(self.G_layers):
            layer_name = 'hl_{}'.format(i)
            G_hl_dict[layer_name] = Dropout(self.dropout_rate)(
                Dense(l_dim, activation=self.activation_hidden)(last_layer))
            last_layer = G_hl_dict[layer_name]

        G_out = Dense(self.n_features_, activation=self.output_activation)(last_layer)

        self.generator = Model(inputs=(G_in), outputs=[G_out])
        self.hist_loss_generator = []

        #### Discriminator #####
        D_in = Input(shape=(self.n_features_,), name='I1')
        D_1 = Dropout(self.dropout_rate, input_shape=(self.n_features_,))(D_in)
        last_layer = D_1

        D_hl_dict = {}
        for i, l_dim in enumerate(self.D_layers):
            layer_name = 'hl_{}'.format(i)
            D_hl_dict[layer_name] = Dropout(self.dropout_rate)(
                Dense(l_dim, activation=self.activation_hidden)(last_layer))
            last_layer = D_hl_dict[layer_name]

        classifier_node = Dense(1, activation='sigmoid')(last_layer)

        self.discriminator = Model(inputs=(D_in), outputs=[classifier_node, D_hl_dict[
            'hl_{}'.format(self.index_D_layer_for_recon_error)]])
        self.hist_loss_discriminator = []

        # Set optimizer
        opt = Adam(learning_rate=self.learning_rate)
        self.generator.compile(optimizer=opt)
        self.discriminator.compile(optimizer=opt)

    def plot_learning_curves(self, start_ind=0, window_smoothening=10):  # pragma: no cover
        fig = plt.figure(figsize=(12, 5))

        l_gen = pd.Series(self.hist_loss_generator[start_ind:]).rolling(window_smoothening).mean()
        l_disc = pd.Series(self.hist_loss_discriminator[start_ind:]).rolling(window_smoothening).mean()

        ax = fig.add_subplot(1, 2, 1)
        ax.plot(range(len(l_gen)), l_gen, )
        ax.set_title('Generator')
        ax.set_ylabel('Loss')
        ax.set_ylabel('Iter')

        ax = fig.add_subplot(1, 2, 2)
        ax.plot(range(len(l_disc)), l_disc)
        ax.set_title('Discriminator')
        ax.set_ylabel('Loss')
        ax.set_xlabel('Iter')

        plt.show()

    def train_step(self, data):
        cross_entropy = tf.keras.losses.BinaryCrossentropy(from_logits=False)
        X_original, latent_noise = data

        with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
            X_gen = self.generator({'I1': latent_noise}, training=True)

            real_output, _ = self.discriminator({'I1': X_original}, training=True)
            fake_output, _ = self.discriminator({'I1': X_gen}, training=True)

            # Correctly predicted
            loss_discriminator = cross_entropy(tf.ones_like(fake_output), fake_output)
            total_loss_generator = loss_discriminator

            ## Losses discriminator                  
            real_loss = cross_entropy(tf.ones_like(real_output, dtype='float32') * 0.9,
                                      real_output)  # one-sided label smoothening
            fake_loss = cross_entropy(tf.zeros_like(fake_output), fake_output)
            total_loss_discriminator = real_loss + fake_loss

        # Compute gradients
        gradients_gen = gen_tape.gradient(total_loss_generator, self.generator.trainable_variables)
        # Update weights
        self.generator.optimizer.apply_gradients(zip(gradients_gen, self.generator.trainable_variables))

        # Compute gradients
        gradients_disc = disc_tape.gradient(total_loss_discriminator, self.discriminator.trainable_variables)
        # Update weights
        self.discriminator.optimizer.apply_gradients(zip(gradients_disc, self.discriminator.trainable_variables))

        self.hist_loss_generator.append(np.float64(total_loss_generator.numpy()))
        self.hist_loss_discriminator.append(np.float64(total_loss_discriminator.numpy()))

    def fit_query(self, query_sample):

        assert (query_sample.shape[0] == 1)
        assert (query_sample.shape[1] == self.n_features_)

        # Make pseudo input (just zeros)
        zeros = np.zeros((1, self.latent_dim_G))

        ### build model for back-propagating a approximate latent space where reconstruction with
        # query sample is optimal ###
        pseudo_in = Input(shape=(self.latent_dim_G,), name='I1')
        z_gamma = Dense(self.latent_dim_G, activation=None, use_bias=True)(pseudo_in)

        sample_gen = self.generator({'I1': z_gamma}, training=False)
        _, sample_disc_latent = self.discriminator({'I1': sample_gen}, training=False)

        self.query_model = Model(inputs=(pseudo_in), outputs=[z_gamma, sample_gen, sample_disc_latent])

        opt = Adam(learning_rate=self.learning_rate_query)
        self.query_model.compile(optimizer=opt)

        ###############
        for i in range(self.epochs_query):
            if ((i % 25 == 0) and (self.verbose == 1)):
                print('iter:', i)

            with tf.GradientTape() as tape:

                z, sample_gen, sample_disc_latent = self.query_model({'I1': zeros}, training=True)

                _, sample_disc_latent_original = self.discriminator({'I1': query_sample}, training=False)

                # Reconstruction loss generator
                abs_err = tf.keras.backend.abs(query_sample - sample_gen)
                loss_recon_gen = tf.keras.backend.mean(tf.keras.backend.mean(abs_err, axis=-1))

                # Reconstruction loss latent space of discrimator
                abs_err = tf.keras.backend.abs(sample_disc_latent_original - sample_disc_latent)
                loss_recon_disc = tf.keras.backend.mean(tf.keras.backend.mean(abs_err, axis=-1))
                total_loss = loss_recon_gen + loss_recon_disc  # equal weighting both terms

            # Compute gradients
            gradients = tape.gradient(total_loss, self.query_model.trainable_variables[0:2])
            # Update weights
            self.query_model.optimizer.apply_gradients(zip(gradients, self.query_model.trainable_variables[0:2]))

        return total_loss.numpy()

    def fit(self, X, y=None):
        """Fit detector. y is ignored in unsupervised methods.

        Parameters
        ----------
        X : numpy array of shape (n_samples, n_features)
            The input samples.

        y : Ignored
            Not used, present for API consistency by convention.

        Returns
        -------
        self : object
            Fitted estimator.
        """
        # validate inputs X and y (optional)
        X = check_array(X)
        self._set_n_classes(y)

        # Verify and construct the hidden units
        self.n_samples_, self.n_features_ = X.shape[0], X.shape[1]
        self._build_model()

        # Standardize data for better performance
        if self.preprocessing:
            self.scaler_ = StandardScaler()
            X_norm = self.scaler_.fit_transform(X)
        else:
            X_norm = np.copy(X)

        for n in range(self.epochs):
            if ((n % 100 == 0) and (n != 0) and (self.verbose == 1)):
                print('Train iter:{}'.format(n))

            # Shuffle train 
            np.random.shuffle(X_norm)

            X_train_sel = X_norm[0: min(self.batch_size, self.n_samples_), :]
            latent_noise = np.random.normal(0, 1, (X_train_sel.shape[0], self.latent_dim_G))

            self.train_step((np.float32(X_train_sel),
                             np.float32(latent_noise)))

        # Predict on X itself and calculate the reconstruction error as
        # the outlier scores. Noted X_norm was shuffled has to recreate
        if self.preprocessing:
            X_norm = self.scaler_.transform(X)
        else:
            X_norm = np.copy(X)

        scores = []
        # For each sample we use a few backpropagation steps, to obtain a point in the latent 
        # space, that best resembles the query sample
        for i in range(X_norm.shape[0]):
            if (self.verbose == 1):
                print('query sample {} / {}'.format(i + 1, X_norm.shape[0]))

            sample = X_norm[[i],]
            score = self.fit_query(sample)
            scores.append(score)

        self.decision_scores_ = np.array(scores)

        self._process_decision_scores()
        return self

    def decision_function(self, X):
        """Predict raw anomaly score of X using the fitted detector.

        The anomaly score of an input sample is computed based on different
        detector algorithms. For consistency, outliers are assigned with
        larger anomaly scores.

        Parameters
        ----------
        X : numpy array of shape (n_samples, n_features)
            The training input samples. Sparse matrices are accepted only
            if they are supported by the base estimator.

        Returns
        -------
        anomaly_scores : numpy array of shape (n_samples,)
            The anomaly score of the input samples.
        """
        check_is_fitted(self, ['decision_scores_'])
        X = check_array(X)

        if self.preprocessing:
            X_norm = self.scaler_.transform(X)
        else:
            X_norm = np.copy(X)

        # Predict on X 
        pred_scores = []
        for i in range(X_norm.shape[0]):
            if (self.verbose == 1):
                print('query sample {} / {}'.format(i + 1, X_norm.shape[0]))

            sample = X_norm[[i],]
            score = self.fit_query(sample)
            pred_scores.append(score)

        pred_scores = np.array(pred_scores)

        return pred_scores
