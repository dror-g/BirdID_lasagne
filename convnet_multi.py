from __future__ import division, absolute_import
from __future__ import print_function, unicode_literals

import time
import numpy as np
import sklearn.cross_validation
import sklearn.metrics
import theano
import theano.tensor as T
import lasagne
import sys, os.path
from scipy.misc import imresize, imread
import multiprocessing

# ############################### prepare data ###############################

def addFiles(file, DIR, foldername, num):
  img = imread(DIR +"/" + foldername + "/" + file)
  img = imresize(img, (128, 128))
  return [num, img]

def validate(batch_size, batch_num, X_valid, y_valid, num):
  batch_slice = slice(batch_size * batch_num,	batch_size * (batch_num + 1))
  X_batch = X_valid[batch_slice]
  y_batch = y_valid[batch_slice]
  loss, probabilities_batch = valid_fn(X_batch, y_batch)
  return [num, {'valid':loss, 'prob':probabilities_batch}]


PER_CATEGORY = 98
CATEGORIES = 9
DIR = "./images"

folders = os.listdir(DIR)
features1 = ( )
for foldername in folders:
  if foldername == ".DS_Store":
    continue
  files = os.listdir(DIR +"/" + foldername)
  if ".DS_Store" in files:
    files.remove(".DS_Store")
  if len(files) > PER_CATEGORY:
    files = sklearn.cross_validation.train_test_split(files, random_state=0, train_size=PER_CATEGORY)[0] # discarding the "test" split
  pool = multiprocessing.Pool(processes=multiprocessing.cpu_count())
  results = [pool.apply_async(addFiles, args=(file, DIR, foldername, i)) for i, file in enumerate(files)]
  feats = [p.get() for p in results]
  pool.terminate()
  features = []
  sorted(feats)
  for feat in feats:
    feat.pop(0)
  for feat in feats:
    features.append(feat[0])
  for feat in features:
    features1 = features1 + (feat,)

features1 = np.array(list(features1))
features1 = features1.astype(theano.config.floatX) / 255.0 - 0.5
features1 = features1.transpose( (0, 3, 1, 2) ) #(h, w, channel) to (channel, h, w)

# Generate labels
label1 = np.zeros(PER_CATEGORY)
for index in range(CATEGORIES - 1):
  arr= np.full((PER_CATEGORY,), index + 1)
  label1 = np.append(label1, arr, axis=0)

label1 = label1.astype("int32")

#Split into training and validation sets
X_train, X_valid, y_train, y_valid = sklearn.cross_validation.train_test_split(
    features1,
    label1,
    random_state=0,
    train_size=0.6,
    )

# Calculate the mean of the training set
mean = np.zeros((3, 128, 128), theano.config.floatX)
for img in X_train:
  mean = mean + img / len(X_train)

# The mean is subtracted (this utilizes the broadcasting feature of numpy)
X_train = X_train - mean
X_valid = X_valid - mean


# ############################## prepare model ##############################

# - conv layers take in 4-tensors with the following dimensions:
#   (batch size, number of channels, image dim 1, image dim 2)
# - the batch size can be provided as `None` to make the network
#   work for multiple different batch sizes
l_in = lasagne.layers.InputLayer(
    shape=(None, 3, 128, 128),
    )

l_pad1 = lasagne.layers.PadLayer(
    l_in,
    width=2,#padding width
    )

l_conv1 = lasagne.layers.Conv2DLayer(
    #l_in,
    l_pad1,
    num_filters=16,
    filter_size=(5, 5),
    nonlinearity=lasagne.nonlinearities.rectify,
    W=lasagne.init.GlorotUniform(gain='relu'),
    )

l_pool1 = lasagne.layers.MaxPool2DLayer(l_conv1, 
    pool_size=(2, 2),
    stride=2,
    )



l_pad2 = lasagne.layers.PadLayer(
    l_pool1,
    width=2,#padding width
    )

l_conv2 = lasagne.layers.Conv2DLayer(
    #l_pool1,
    l_pad2,
    num_filters=32,
    filter_size=(5, 5),
    nonlinearity=lasagne.nonlinearities.rectify,
    W=lasagne.init.GlorotUniform(gain='relu'),
    )

l_pool2 = lasagne.layers.MaxPool2DLayer(l_conv2, 
    pool_size=(2, 2),
    stride=2,
    )


l_pad3 = lasagne.layers.PadLayer(
    l_pool2,
    width=2,#padding width
    )

l_conv3 = lasagne.layers.Conv2DLayer(
    #l_pool2,
    l_pad3,
    num_filters=64,
    filter_size=(5, 5),
    nonlinearity=lasagne.nonlinearities.rectify,
    W=lasagne.init.GlorotUniform(gain='relu'),
    )

l_pool3 = lasagne.layers.MaxPool2DLayer(l_conv3, 
    pool_size=(2, 2),
    stride=2,
    )

"""
l_hidden1 = lasagne.layers.DenseLayer(
    l_pool2,
    num_units=1028,
    nonlinearity=lasagne.nonlinearities.rectify,
    W=lasagne.init.GlorotUniform(),
)

l_hidden1_dropout = lasagne.layers.DropoutLayer(l_hidden1, p=0.5)
"""

# - applies the softmax after computing the final layer units
l_out = lasagne.layers.DenseLayer(
    #l_hidden1,
    l_pool3,
    num_units=CATEGORIES,
    nonlinearity=lasagne.nonlinearities.softmax,
    #W=lasagne.init.GlorotUniform(),
    )

# ############################### network loss ###############################

l2_regularization_rate = 0.0001

# int32 vector
target_vector = T.ivector('y')


def loss_fn(output):
  return T.mean(lasagne.objectives.categorical_crossentropy(output,
    target_vector))

stochastic_out = lasagne.layers.get_output(l_out)
# - every layer is passed the deterministic=True flag, but in this
#   case, only the dropout layer actually uses it
deterministic_out = lasagne.layers.get_output(l_out, deterministic=True)

# - theano variable for non-deterministic loss (ie. with dropout)
stochastic_loss = loss_fn(stochastic_out) + l2_regularization_rate * lasagne.regularization.l2(stochastic_out)
# - theano variable for deterministic (ie. without dropout)
deterministic_loss = loss_fn(deterministic_out)

# ######################## compiling theano functions ########################

print("Compiling theano functions")

# - takes out all weight tensors from the network, in order to compute
#   how the weights should be updated
all_params = lasagne.layers.get_all_params(l_out)

# - calculate how the parameters should be updated
# - theano keeps a graph of operations, so that gradients w.r.t.
#   the loss can be calculated
"""
updates = lasagne.updates.nesterov_momentum(
    loss_or_grads=stochastic_loss,
    params=all_params,
    learning_rate=0.1,
    momentum=0.9)
"""
updates = lasagne.updates.adagrad(
    loss_or_grads=stochastic_loss,
    params=all_params,
    learning_rate=0.01,
    #other params left as default as recommended in the documentation
    )

# - create a function that also updates the weights
# - this function takes in 2 arguments: the input batch of images and a
#   target vector (the y's) and returns a list with a single scalar
#   element (the loss)
train_fn = theano.function(inputs=[l_in.input_var, target_vector],
    outputs=[stochastic_loss, stochastic_out],
    updates=updates)

# - create a function that does not update the weights, and doesn't
#   use dropout
# - same interface as previous the previous function, but now the
#   output is a list where the first element is the loss, and the
#   second element is the actual predicted probabilities for the
#   input data
valid_fn = theano.function(inputs=[l_in.input_var, target_vector],
    outputs=[deterministic_loss, deterministic_out])

# ################################# training #################################

print("Starting training...")

num_epochs = 301
batch_size = 1
for epoch_num in range(num_epochs):
  start_time = time.time()
  # iterate over training minibatches and update the weights
  num_batches_train = int(np.ceil(len(X_train) / batch_size))
  train_losses = []
  list_of_probabilities_batch = []
  for batch_num in range(num_batches_train):
    batch_slice = slice(batch_size * batch_num,
        batch_size * (batch_num + 1))
    X_batch = X_train[batch_slice]
    y_batch = y_train[batch_slice]

    loss, probabilities_batch = train_fn(X_batch, y_batch)
    train_losses.append(loss)
    list_of_probabilities_batch.append(probabilities_batch)
  # aggregate training losses for each minibatch into scalar
  train_loss = np.mean(train_losses)
  # concatenate probabilities for each batch into a matrix
  probabilities = np.concatenate(list_of_probabilities_batch)
  # calculate classes from the probabilities
  predicted_classes = np.argmax(probabilities, axis=1)
  # calculate accuracy for this epoch
  train_accuracy = sklearn.metrics.accuracy_score(y_train, predicted_classes)

  # calculate validation loss
  num_batches_valid = int(np.ceil(len(X_valid) / batch_size))
  valid_losses = []
  list_of_probabilities_batch = []
  pool = multiprocessing.Pool(processes=multiprocessing.cpu_count())
  results = [pool.apply_async(validate, args=(batch_size, batch_num, X_valid, y_valid, i)) for i, batch_num in enumerate(range(num_batches_valid))]
  losses = [p.get() for p in results]
  pool.terminate()
  sorted(losses)
  for loss in losses:
    valid_losses.append(loss[1]['valid'])
    list_of_probabilities_batch.append(loss[1]['prob'])
  valid_loss = np.mean(valid_losses)
    # concatenate probabilities for each batch into a matrix
  probabilities = np.concatenate(list_of_probabilities_batch)
  # calculate classes from the probabilities
  predicted_classes = np.argmax(probabilities, axis=1)
  # calculate accuracy for this epoch
  accuracy = sklearn.metrics.accuracy_score(y_valid, predicted_classes)

  total_time = time.time() - start_time
  print("Epoch: %d, train_loss=%f, train_accuracy=%f, valid_loss=%f, valid_accuracy=%f, time=%fs"
      % (epoch_num + 1, train_loss, train_accuracy, valid_loss, accuracy, total_time))
