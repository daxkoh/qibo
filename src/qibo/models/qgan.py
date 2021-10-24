import numpy as np
import tensorflow as tf
from numpy.random import randn
from tensorflow.keras.models import Sequential
from tensorflow.keras.optimizers import Adadelta
from tensorflow.keras.layers import Dense, Conv2D, Dropout, Reshape, LeakyReLU, Flatten
from qibo import gates, hamiltonians, models, set_backend


class qGAN(object):
    """Model that implements and trains a style-based quantum generative adversarial network.

    For original manuscript: `arXiv:2110.06933 <https://arxiv.org/abs/2110.06933>`_

    Args:
        oracle (:class:`qibo.core.circuit.Circuit`): quantum circuit that flips
            the sign using a Grover ancilla initialized with -X-H-. Grover ancilla
            expected to be last qubit of oracle circuit.
        superposition_circuit (:class:`qibo.core.circuit.Circuit`): quantum circuit that
            takes an initial state to a superposition. Expected to use the first
            set of qubits to store the relevant superposition.
        initial_state_circuit (:class:`qibo.core.circuit.Circuit`): quantum circuit
            that initializes the state. If empty defaults to ``|000..00>``
        superposition_qubits (int): number of qubits that store the relevant superposition.
            Leave empty if superposition does not use ancillas.
        superposition_size (int): how many states are in a superposition.
            Leave empty if its an equal superposition of quantum states.
        number_solutions (int): number of expected solutions. Needed for normal Grover.
            Leave empty for iterative version.
        target_amplitude (float): absolute value of the amplitude of the target state. Only for
            advanced use and known systems.
        check (function): function that returns True if the solution has been
            found. Required of iterative approach.
            First argument should be the bitstring to check.
        check_args (tuple): arguments needed for the check function.
            The found bitstring not included.
        iterative (bool): force the use of the iterative Grover

    Example:
        .. testcode::

            import numpy as np
            from qibo.models.qgan import qGAN
            # Create reference distribution. Ex: 3D correlated Gaussian distribution normalized between [-1,1]
            reference_distribution = []
            samples = 10000
            mean = [0, 0, 0]
            cov = [[0.5, 0.1, 0.25], [0.1, 0.5, 0.1], [0.25, 0.1, 0.5]]
            x, y, z = np.random.multivariate_normal(mean, cov, samples).T/4
            s1 = np.reshape(x, (samples,1))
            s2 = np.reshape(y, (samples,1))
            s3 = np.reshape(z, (samples,1))
            reference_distribution = np.hstack((s1,s2,s3))
            # Train qGAN with your particular setup
            train_qGAN = qGAN(reference_distribution, 1, 3)
            train_qGAN()
    """

    def __init__(self, reference, layers, latent_dim, batch_samples=128, n_epochs=20000, lr=0.5):

        self.reference = reference
        self.nqubits = reference.shape[1]
        self.layers = layers
        self.latent_dim = latent_dim
        self.training_samples = reference.shape[0]
        self.batch_samples = batch_samples
        self.n_epochs = n_epochs
        self.lr = lr

    def define_discriminator(self, alpha=0.2, dropout=0.2):
        """define the standalone discriminator model"""
        model = Sequential()           
        model.add(Dense(200, use_bias=False, input_dim=self.nqubits))
        model.add(Reshape((10,10,2)))       
        model.add(Conv2D(64, kernel_size=3, strides=1, padding='same', kernel_initializer='glorot_normal'))
        model.add(LeakyReLU(alpha=alpha))       
        model.add(Conv2D(32, kernel_size=3, strides=1, padding='same', kernel_initializer='glorot_normal'))
        model.add(LeakyReLU(alpha=alpha))    
        model.add(Conv2D(16, kernel_size=3, strides=1, padding='same', kernel_initializer='glorot_normal'))
        model.add(LeakyReLU(alpha=alpha))    
        model.add(Conv2D(8, kernel_size=3, strides=1, padding='same', kernel_initializer='glorot_normal'))    
        model.add(Flatten())
        model.add(LeakyReLU(alpha=alpha))
        model.add(Dropout(dropout))     
        model.add(Dense(1, activation='sigmoid'))
        
        # compile model
        opt = Adadelta(learning_rate=0.1)
        model.compile(loss='binary_crossentropy', optimizer=opt, metrics=['accuracy'])
        return model

    def set_params(self, circuit, params, x_input, i):
        p = []
        index = 0
        noise = 0
        for l in range(self.layers):
            for q in range(self.nqubits):
                p.append(params[index]*x_input[noise][i] + params[index+1])
                index+=2
                noise=(noise+1)%self.latent_dim
                p.append(params[index]*x_input[noise][i] + params[index+1])
                index+=2
                p.append(params[index]*x_input[noise][i] + params[index+1])
                index+=2
                noise=(noise+1)%self.latent_dim
                p.append(params[index]*x_input[noise][i] + params[index+1])
                index+=2
                noise=(noise+1)%self.latent_dim
            for i in range(0, self.nqubits-1):
                p.append(params[index]*x_input[noise][i] + params[index+1])
                index+=2
                noise=(noise+1)%self.latent_dim
            p.append(params[index]*x_input[noise][i] + params[index+1])
            index+=2
            noise=(noise+1)%self.latent_dim
        for q in range(self.nqubits):
            p.append(params[index]*x_input[noise][i] + params[index+1])
            index+=2
            noise=(noise+1)%self.latent_dim
        circuit.set_parameters(p)

    def generate_latent_points(self, samples):
        """generate points in latent space as input for the generator"""
        # generate points in the latent space
        x_input = randn(self.latent_dim * samples)
        # reshape into a batch of inputs for the network
        x_input = x_input.reshape(samples, self.latent_dim)
        return x_input
    
    def generate_fake_samples(self, params, samples, circuit, hamiltonians_list):
        """use the generator to generate fake examples, with class labels"""
        # generate points in latent space
        x_input = self.generate_latent_points(samples)
        x_input = np.transpose(x_input)
        # generator outputs
        X = []
        for i in range(self.nqubits):
            X.append([])
        # quantum generator circuit
        for i in range(samples):
            self.set_params(circuit, params, x_input, i)
            circuit_execute = circuit.execute()
            for ii in range(self.nqubits):
                X[ii].append(hamiltonians_list[ii].expectation(circuit_execute))
        # shape array
        X = tf.stack([X[i] for i in range(len(X))], axis=1)
        # create class labels
        y = np.zeros((samples, 1))
        return X, y
    
    def define_cost_gan(self, params, discriminator, samples, circuit, hamiltonians_list):
        """define the combined generator and discriminator model, for updating the generator"""
        # generate fake samples
        x_fake, y_fake = self.generate_fake_samples(params, samples, circuit, hamiltonians_list)
        # create inverted labels for the fake samples
        y_fake = np.ones((samples, 1))
        # evaluate discriminator on fake examples
        disc_output = discriminator(x_fake)
        loss = tf.keras.losses.binary_crossentropy(y_fake, disc_output)
        loss = tf.reduce_mean(loss)
        return loss
    
    def train(self, d_model, circuit, hamiltonians_list):
        """train the generator and discriminator"""
        
        def generate_real_samples(samples, distribution, real_samples):
            """generate real samples with class labels"""
            # generate samples from the distribution
            idx = np.random.randint(real_samples, size=samples)
            X = distribution[idx,:]
            # generate class labels
            y = np.ones((samples, 1))
            return X, y
        
        d_loss = []
        g_loss = []
        # determine half the size of one batch, for updating the discriminator
        half_samples = int(self.batch_samples / 2)
        initial_params = tf.Variable(np.random.uniform(-0.15, 0.15, 10*self.layers*self.nqubits + 2*self.nqubits))
        optimizer = tf.optimizers.Adadelta(learning_rate=self.lr)
        # prepare real samples
        s = self.reference
        # manually enumerate epochs
        for i in range(self.n_epochs):
            # prepare real samples
            x_real, y_real = generate_real_samples(half_samples, s, self.training_samples)
            # prepare fake examples
            x_fake, y_fake = self.generate_fake_samples(initial_params, half_samples, circuit, hamiltonians_list)
            # update discriminator
            d_loss_real, _ = d_model.train_on_batch(x_real, y_real)
            d_loss_fake, _ = d_model.train_on_batch(x_fake, y_fake)
            d_loss.append((d_loss_real + d_loss_fake)/2)
            # update generator
            with tf.GradientTape() as tape:
                loss = self.define_cost_gan(initial_params, d_model, self.batch_samples, circuit, hamiltonians_list)
            grads = tape.gradient(loss, initial_params)
            optimizer.apply_gradients([(grads, initial_params)])
            g_loss.append(loss)
            np.savetxt(f"PARAMS_3Dgaussian_{self.nqubits}_{self.latent_dim}_{self.layers}_{self.training_samples}_{self.batch_samples}_{self.lr}", [initial_params.numpy()], newline='')
            np.savetxt(f"dloss_3Dgaussian_{self.nqubits}_{self.latent_dim}_{self.layers}_{self.training_samples}_{self.batch_samples}_{self.lr}", [d_loss], newline='')
            np.savetxt(f"gloss_3Dgaussian_{self.nqubits}_{self.latent_dim}_{self.layers}_{self.training_samples}_{self.batch_samples}_{self.lr}", [g_loss], newline='')
            # serialize weights to HDF5
            d_model.save_weights(f"discriminator_3Dgaussian_{self.nqubits}_{self.latent_dim}_{self.layers}_{self.training_samples}_{self.batch_samples}_{self.lr}.h5")



    def execute(self):
        """Execute qGAN training.

        If the number of solutions is given, calculates iterations,
        otherwise it uses an iterative approach.

        Args:
            nshots (int): number of shots in order to get the frequencies.
            freq (bool): print the full frequencies after the exact Grover algorithm.

        Returns:
            solution (str): bitstring (or list of bitstrings) measured as solution of the search.
            iterations (int): number of oracle calls done to reach a solution.
        """
        # set qibo backend
        set_backend('tensorflow')
        
        # create classical discriminator
        discriminator = self.define_discriminator()
        
        # define hamiltonian to generate fake samples
        def hamiltonian(nqubits, position):
            identity = [[1, 0], [0, 1]]
            m0 = hamiltonians.Z(1).matrix
            kron = []
            for i in range(nqubits):
                if i == position:
                    kron.append(m0)
                else:
                    kron.append(identity)
            for i in range(nqubits - 1):
                if i==0:
                    ham = np.kron(kron[i+1], kron[i])
                else:
                    ham = np.kron(kron[i+1], ham)
            ham = hamiltonians.Hamiltonian(nqubits, ham)
            return ham
        
        hamiltonians_list = []
        for i in range(self.nqubits):
            hamiltonians_list.append(hamiltonian(self.nqubits, i))
        
        # create quantum generator
        circuit = models.Circuit(self.nqubits)
        for l in range(self.layers):
            for q in range(self.nqubits):
                circuit.add(gates.RY(q, 0))
                circuit.add(gates.RZ(q, 0))
                circuit.add(gates.RY(q, 0))
                circuit.add(gates.RZ(q, 0))
            for i in range(0, self.nqubits-1):
                circuit.add(gates.CRY(i, i+1, 0))
            circuit.add(gates.CRY(self.nqubits-1, 0, 0))
        for q in range(self.nqubits):
            circuit.add(gates.RY(q, 0))
        
        # train model
        self.train(discriminator, circuit, hamiltonians_list)

    def __call__(self):
        """Equivalent to :meth:`qibo.models.qgan.qGAN.execute`."""
        return self.execute()
