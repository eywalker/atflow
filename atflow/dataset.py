import numpy as np
import tensorflow as tf
from tensorflow.python.util import nest

from atflow.misc import batchify_shape


def create_placeholders_like(input_shape, dtype=tf.float32, name='inputs_{index}'):
    flat_inputs_ = []
    for i, shape in enumerate(nest.flatten(input_shape)):
        flat_inputs_.append(tf.placeholder(tf.float32, shape=shape, name=name.format(index=i)))
    inputs_ = nest.pack_sequence_as(input_shape, flat_inputs_)
    return inputs_


def batchify(data, batchsize=128, n_epochs=1, shuffle=True, cutoff=False):
    """
    Creates and returns a generator that will yield batches of data
    with batchsize for n_epochs. If n_epochs <= 0, then the generator
    yields batches without an end. If cutoff=True, then only complete
    batches will be returned. If cutoff=False (default), any remaining
    elements will be returned for the last batch.
    """
    flat_data = nest.flatten(data)

    def get_data(indicies):
        batch = [x[indicies] for x in flat_data]
        return nest.pack_sequence_as(data, batch)

    N = len(flat_data[0])

    def get_perm():
        if shuffle:
            return np.random.permutation(N)
        else:
            return np.arange(N)

    perm = get_perm()
    pos = 0
    epoch = 0
    while n_epochs <= 0 or epoch < n_epochs - 1 or pos < N:
        if pos + batchsize > N:
            if epoch == n_epochs - 1:
                if not cutoff:
                    yield get_data(perm[pos:])
                return
            else:
                missing = pos + batchsize - N
                pos1 = perm[pos:]
                perm = get_perm()

                pos2 = perm[:missing]
                yield get_data(np.hstack((pos1, pos2)))
                epoch += 1
                pos = missing
        else:
            yield get_data(perm[pos:pos + batchsize])
            pos = pos + batchsize


class Dataset:
    """
    Represents a collection of dataset
    """

    def __init__(self,
                 inputs, targets,
                 test_inputs=None, test_targets=None,
                 validation_inputs=None, validation_targets=None,
                 seed=None, train_frac=0.8,
                 inputs_label=None, targets_label=None,
                 **kwargs):
        """
       Initialize Dataset using existing data. You must at least provide inputs and targets data where the first
       dimension is the batch dimension. Validation inputs & targets and test inputs & targets are optional. If
       validation inputs and targets are not provided and if `train_frac` > 0, then random `train_frac` fraction of
       the inputs and targets are used as the actual training set and the rest as the validation set (defaults to
       80% used as training set and 20% as validation set). Setting `seed` forces the randomization seed to be set
       for this split. If `validation_inputs` and `validation_targets` are given or if `train_frac` <= 0,
       then `inputs` and `targets` are used as the training set inputs and targets.
       Set `normalize` to True to have all inputs normalized (mean subtracted and divided by standard
       deviation) according to the mean and std of the `inputs`.
       :param inputs: inputs data. First dimension is interpreted as `batch`.
       :param targets: targets data. First dimension is interpreted as `batch`.
       :param test_inputs: optional test inputs
       :param test_targets: optional test targets
       :param validation_inputs: optional validation inputs
       :param validation_targets: optional validation targets
       :param seed: if not None, will be used when splitting inputs into train set and validation set (if applicable)
       :param train_frac: fraction to use if splitting inputs into train and validation set. Defaults to 80:20 split.
       """

        # keep inputs and targets in its structured form
        flat_inputs = nest.flatten(inputs)
        flat_targets = nest.flatten(targets)

        if inputs_label is None:
            inputs_label = ['inputs{}'.format(i) for i in range(len(flat_inputs))]
        else:
            nest.assert_same_structure(inputs_label, inputs)
            inputs_label = nest.flatten(inputs_label)

        if targets_label is None:
            targets_label = ['targets{}'.format(i) for i in range(len(flat_targets))]
        else:
            nest.assert_same_structure(targets_label, targets)
            targets_label = nest.flatten(targets_label)
        self._inputs_label = inputs_label
        self._targets_label = targets_label

        n_inputs = len(flat_inputs[0])

        def has_same_batch(data):
            d = len(data[0])
            return all(map(lambda x: len(x) == d, data[1:]))

        if not has_same_batch(flat_inputs + flat_targets):
            raise ValueError('All inputs and targets must share same batch size')

        self._inputs_shape = [tf.TensorShape(batchify_shape(x.shape)) for x in flat_inputs]
        self._targets_shape = [tf.TensorShape(batchify_shape(t.shape)) for t in flat_targets]

        self.inputs_structure = nest.pack_sequence_as(inputs, range(len(flat_inputs)))
        self.targets_structure = nest.pack_sequence_as(targets, range(len(flat_targets)))

        # verify shape consistencies
        if test_inputs is not None:
            # TODO: consider adding explicit checks for the shape of individual tensors
            nest.assert_same_structure(test_inputs, self.inputs_structure)
            nest.assert_same_structure(test_targets, self.targets_structure)
            test_inputs = nest.flatten(test_inputs)
            test_targets = nest.flatten(test_targets)
            if not has_same_batch(test_inputs + test_targets):
                raise ValueError('All test inputs and targets must share same batch size')

        if validation_inputs is not None:
            nest.assert_same_structure(validation_inputs, self.inputs_structure)
            nest.assert_same_structure(validation_targets, self.targets_structure)
            validation_inputs = nest.flatten(validation_inputs)
            validation_targets = nest.flatten(validation_targets)
            if not has_same_batch(validation_inputs + validation_targets):
                raise ValueError('All validation inputs and targets must share same batch size')

        # prepare validation set
        if validation_inputs is None and train_frac > 0.0:
            if seed:
                np.random.seed(seed)
            perm = np.random.permutation(n_inputs)
            split = round(n_inputs * train_frac)
            self.train_idx = sorted(perm[:split])
            self.validation_idx = sorted(perm[split:])

            validation_inputs = [x[self.validation_idx] for x in flat_inputs]
            validation_targets = [t[self.validation_idx] for t in flat_targets]

            train_inputs = [x[self.train_idx] for x in flat_inputs]
            train_targets = [t[self.train_idx] for t in flat_targets]
        else:
            train_inputs = flat_inputs
            train_targets = flat_targets

        self._train_inputs, self._train_targets = train_inputs, train_targets
        self._validation_inputs, self._validation_targets = validation_inputs, validation_targets
        self._test_inputs, self._test_targets = test_inputs, test_targets

        self.update_stats()

        self.minibatch_idx = 0
        self.train_perm = np.arange(self.n_train_samples)
        self._minibatch_indicies = None
        self.info = kwargs
        self.next_epoch()

        self.normalizer = lambda data: data

    @property
    def inputs_label(self):
        """
        Returns: labels on the inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_label)

    @property
    def targets_label(self):
        """
        Returns: labels on the targets
        """
        return nest.pack_sequence_as(self.targets_structure, self._targets_label)

    @property
    def train_inputs(self):
        """
        Returns: training set inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._train_inputs)

    @property
    def train_targets(self):
        """
        Returns: training set targets
        """
        return nest.pack_sequence_as(self.targets_structure, self._train_targets)

    @property
    def test_inputs(self):
        """
        Returns: test set inputs
        """
        if self._test_inputs is None:
            return None
        else:
            return nest.pack_sequence_as(self.inputs_structure, self._test_inputs)

    @property
    def test_targets(self):
        """
        Returns: test set targets
        """
        if self._test_targets is None:
            return None
        else:
            return nest.pack_sequence_as(self.targets_structure, self._test_targets)

    @property
    def validation_inputs(self):
        """
        Returns: validaton set inputs
        """
        if self._validation_inputs is None:
            return None
        else:
            return nest.pack_sequence_as(self.inputs_structure, self._validation_inputs)

    @property
    def validation_targets(self):
        """
        Returns: validation set targets
        """
        if self._validation_targets is None:
            return None
        else:
            return nest.pack_sequence_as(self.targets_structure, self._validation_targets)

    @property
    def inputs_shape(self):
        """
        Returns: shape of the inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_shape)

    @property
    def targets_shape(self):
        """
        Returns: shape of the targets
        """
        return nest.pack_sequence_as(self.targets_structure, self._targets_shape)

    def copy(self):
        """
        Returns: a copy of the dataset
        """
        return Dataset(inputs=self.train_inputs.copy(), targets=self.train_targets.copy(),
                       validation_inputs=self.validation_inputs, validation_targets=self.validation_targets,
                       test_inputs=self.test_inputs, test_targets=self.test_targets)

    def update_stats(self, axis=None):
        """
        Recomputes the statistics based on the current dataset
        """
        def get_stats(inputs, axis=None):
            if axis is None:
                axis = tuple(range(max(inputs.ndim - 1, 1)))
            stats = {}
            mean = np.mean(inputs, axis=axis, keepdims=True)
            std = np.std(inputs, axis=axis, ddof=1, keepdims=True)
            stationary_mean = np.mean(inputs)
            stationary_std = np.std(inputs, ddof=1)
            return mean, std, stationary_mean, stationary_std

        mean, std, stationary_mean, stationay_std = zip(*[get_stats(x, axis=axis) for x in self._train_inputs])
        self._inputs_mean = mean
        self._inputs_std = std
        self._inputs_stationary_mean = stationary_mean
        self._inputs_stationary_std = stationay_std

    @property
    def inputs_mean(self):
        """
        Returns: mean of the training set inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_mean)

    @property
    def inputs_std(self):
        """
        Returns: standard deviation of the training set inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_std)

    @property
    def inputs_stationary_mean(self):
        """
        Returns: stationary mean of the training set inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_stationary_mean)

    @property
    def inputs_stationary_std(self):
        """
        Returns: stationary standard deviation of the training set inputs
        """
        return nest.pack_sequence_as(self.inputs_structure, self._inputs_stationary_std)

    def normalize(self, axis=None, control=None):
        """
        Normalize the dataset
        """
        # TODO: extend to support more complex axis specification
        self.update_stats(axis=axis)
        if control is None:
            control = [True] * len(self._train_inputs)
        else:
            control = nest.flatten(control)

        # establish closure
        inputs_mean = self._inputs_mean
        inputs_std = self._inputs_std

        def normalize_inputs(data):
            flat_data = nest.flatten(data)
            normalized_data = [((d - mu) / sigma if c else d) for d, mu, sigma, c in
                               zip(flat_data, inputs_mean, inputs_std, control)]
            return nest.pack_sequence_as(data, normalized_data)

        self.normalizer = normalize_inputs

        self._train_inputs = normalize_inputs(self._train_inputs)

        if self._test_inputs is not None:
            self._test_inputs = normalize_inputs(self._test_inputs)

        if self._validation_inputs is not None:
            self._validation_inputs = normalize_inputs(self._validation_inputs)

        self.update_stats(axis=axis)

    @property
    def n_train_samples(self):
        """
        :return: the length of the training set
        """
        return len(self._train_inputs[0]) if self._train_inputs is not None else 0

    @property
    def n_test_samples(self):
        """
        :return: the length of the test set
        """
        return len(self._test_inputs[0]) if self._test_inputs is not None else 0

    @property
    def n_validation_samples(self):
        """
        :return: the length of validation set
        """
        return len(self._validation_inputs[0]) if self._validation_inputs is not None else 0

    @property
    def train_set(self):
        """
        Returns the train set tuple
        :return: (inputs, targets) for the training set
        """
        return self.train_inputs, self.train_targets

    @property
    def test_set(self):
        """
        Returns the test set tuple
        :return: (inputs, targets) for the test set
        """
        return self.test_inputs, self.test_targets

    @property
    def validation_set(self):
        """
        Returns the validation set tuple
        :return: (inputs, targets) for the validation set
        """
        return self.validation_inputs, self.validation_targets

    def minibatch(self, batch_size):
        """
        Returns the next minibatch of size `batch_size`. Currently it must be that `batch_size` < `len(inputs)`
        or otherwise it will throw an error.
        :param batch_size: size of the next batch.
        :return: (inputs, targets) tuple of size batch_size
        """
        # TODO: Consider cleaner handling of terminal indicies
        if batch_size > self.n_train_samples:
            raise ValueError('Batch size must be smaller than or equal to total number of samples ({samples})'.format(
                samples=self.n_train_samples))
        inputs, targets = self.train_set
        if self.minibatch_idx + batch_size > self.n_train_samples:
            self.next_epoch()
        idx = self.train_perm[self.minibatch_idx:self.minibatch_idx + batch_size]
        self._minibatch_indicies = idx
        self.minibatch_idx += batch_size

        batch_inputs = nest.pack_sequence_as(self.inputs_structure, [x[idx] for x in self._train_inputs])
        batch_targets = nest.pack_sequence_as(self.targets_structure, [t[idx] for t in self._train_targets])

        return batch_inputs, batch_targets

    @property
    def minibatch_indicies(self):
        return self._minibatch_indicies

    def next_epoch(self, seed=None):
        """
        Starts the next epoch by randomizing batch indicies. Call this to re-randomize the batch sequences.
        You can optionally pass in randomization seed to get repeatable batch sequences.
        :param seed: optional randomization seed to be used when setting index permutation
        """
        self.minibatch_idx = 0
        if seed:
            np.random.seed(seed)
        self.train_perm = np.random.permutation(self.n_train_samples)


class MultiDataset:
    def __init__(self, *datasets):
        self._datasets = datasets

    @property
    def inputs_label(self):
        return [d.inputs_label for d in self._datasets]

    @property
    def targets_label(self):
        return [d.targets_label for d in self._datasets]

    @property
    def train_inputs(self):
        return [d.train_inputs for d in self._datasets]

    @property
    def train_targets(self):
        return [d.train_targets for d in self._datasets]

    @property
    def test_inputs(self):
        return [d.test_inputs for d in self._datasets]

    @property
    def test_targets(self):
        return [d.test_targets for d in self._datasets]

    @property
    def validation_inputs(self):
        return [d.validation_inputs for d in self._datasets]

    @property
    def validation_targets(self):
        return [d.validation_targets for d in self._datasets]

    @property
    def inputs_shape(self):
        return [d.inputs_shape for d in self._datasets]

    @property
    def targets_shape(self):
        return [d.targets_shape for d in self._datasets]

    def update_stats(self, axis=None):
        for d in self._datasets:
            d.update_stats(axis=axis)

    @property
    def inputs_mean(self):
        return [d.inputs_mean for d in self._datasets]

    @property
    def inputs_std(self):
        return [d.inputs_std for d in self._datasets]

    @property
    def inputs_stationary_mean(self):
        return [d.inputs_stationary_mean for d in self._datasets]

    @property
    def inputs_stationary_std(self):
        return [d.inputs_stationary_std for d in self._datasets]

    def normalize(self, axis=None):
        normalizers = []
        for d in self._datasets:
            d.normalize(axis=axis)
            normalizers.append(d.normalizer)

        def normalize_all(data):
            return [n(d) for d, n in zip(data, normalizers)]

        self.normalizer = normalize_all

    @property
    def n_train_samples(self):
        return min(d.n_train_samples for d in self._datasets)

    @property
    def n_test_samples(self):
        return min(d.n_test_samples for d in self._datasets)

    @property
    def n_validation_samples(self):
        return min(d.n_validation_samples for d in self._datasets)

    @property
    def train_set(self):
        """
        Returns the train set tuple
        :return: (inputs, targets) for the training set
        """
        return self.train_inputs, self.train_targets

    @property
    def test_set(self):
        """
        Returns the test set tuple
        :return: (inputs, targets) for the test set
        """
        return self.test_inputs, self.test_targets

    @property
    def validation_set(self):
        """
        Returns the validation set tuple
        :return: (inputs, targets) for the validation set
        """
        return self.validation_inputs, self.validation_targets

    def minibatch(self, batch_size):
        if batch_size > self.n_train_samples:
            raise ValueError('Batch size must be smaller than or equal to total number of samples ({samples})'.format(
                samples=self.n_train_samples))
        minibatches = [dataset.minibatch(batch_size) for dataset in self._datasets]
        batch_inputs, batch_targets = zip(*minibatches)
        return list(batch_inputs), list(batch_targets)

    def next_epoch(self, seed=None):
        for d in self._datasets:
            d.next_epoch(seed=seed)
