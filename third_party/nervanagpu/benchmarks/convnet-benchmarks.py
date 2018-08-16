#!/usr/bin/python
# Copyright 2014 Nervana Systems Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy           as np
import pycuda.driver   as drv
from pycuda.autoinit   import context
from nervanagpu        import NervanaGPU
from nervanagpu.layers import Layer, DataLayer, ConvLayer, PoolLayer, FullLayer, Inception
print(context.get_device().name())

# Compare results here:
# https://github.com/soumith/convnet-benchmarks

# Available nets:
# "Alexnet","Overfeat","VGG","GoogLeNet1","GoogLeNet2","VGG_E"
# Note GoogLeNet2 only fits in fp16 currently.  I need to work out delta sharing in inception layers.
nets = ("Alexnet","Overfeat","VGG","GoogLeNet1","GoogLeNet2","VGG_E")

#Available dtypes: np.float16, np.float32
dtypes = (np.float16,np.float32,)

# number of full iterations
loops       = 10
# show bechmark details for each layer
layer_bench = 0
# show layer stats after each operation
print_stats = 0
# run network with all zeros to see speed difference
zeros       = 0
# print more stuff
verbose     = 0


ng = NervanaGPU(bench=layer_bench)

# common convolutional layer settings
conv11    = { "R":11, "S":11, "pad_h":2, "pad_w":2, "str_h":4, "str_w":4 }
conv11p0  = { "R":11, "S":11, "pad_h":0, "pad_w":0, "str_h":4, "str_w":4 }
conv7     = { "R":7,  "S":7,  "pad_h":3, "pad_w":3, "str_h":2, "str_w":2 }
conv5     = { "R":5,  "S":5,  "pad_h":2, "pad_w":2 }
conv5p0   = { "R":5,  "S":5,  "pad_h":0, "pad_w":0 }
conv3     = { "R":3,  "S":3,  "pad_h":1, "pad_w":1 }
conv2     = { "R":2,  "S":2,  "pad_h":0, "pad_w":0, "str_h":2, "str_w":2 }
conv1     = { "R":1,  "S":1,  "pad_h":0, "pad_w":0 }

# traditional pooling
pool2s2p0 = { "R":2, "S":2 }
pool3s2p0 = { "R":3, "S":3, "str_h":2, "str_w":2 }
pool3s2p1 = { "R":3, "S":3, "str_h":2, "str_w":2, "pad_h":1, "pad_w":1 }
pool3s1p1 = { "R":3, "S":3, "str_h":1, "str_w":1, "pad_h":1, "pad_w":1 }
pool7s1p0 = { "R":7, "S":7, "str_h":1, "str_w":1 }

# maxout pooling
pool1j2 = { "op":"max", "J":2 } # maxout in the fc layers
pool2j2 = { "op":"max", "J":2, "R":2, "S":2 }
pool3j2 = { "op":"max", "J":2, "R":3, "S":3 }

def inception1(conf):
    return {
        "layer":Inception, "partitions" : (
            (
                { "layer":ConvLayer, "common":conv1, "K":conf[0][0], },
            ),
            (
                { "layer":ConvLayer, "common":conv1, "K":conf[1][0], },
                { "layer":ConvLayer, "common":conv3, "K":conf[1][1], },
            ),
            (
                { "layer":ConvLayer, "common":conv1, "K":conf[2][0], },
                { "layer":ConvLayer, "common":conv5, "K":conf[2][1], },
            ),
            (
                { "layer":PoolLayer, "common":pool3s1p1, "op":"max" },
                { "layer":ConvLayer, "common":conv1,      "K":conf[3][0], },
            ),
        )
    }

def inception2(conf):
    layer = { "layer":Inception, "partitions" : [] }
    partitions = layer["partitions"]

    if conf[0][0]:
        partitions.append( ( { "layer":ConvLayer, "K":conf[0][0], "common":conv1 }, ) )

    partitions.extend( (
        (
            { "layer":ConvLayer, "K":conf[1][0], "common":conv1, },
            { "layer":ConvLayer, "K":conf[1][1], "common":conv3, },
        ),
        (
            { "layer":ConvLayer, "K":conf[2][0], "common":conv1, },
            { "layer":ConvLayer, "K":conf[2][1], "common":conv3, },
            { "layer":ConvLayer, "K":conf[2][1], "common":conv3, },
        ),
    ) )
    if conf[3][1]:
        partitions.append( (
            { "layer":PoolLayer, "common":pool3s1p1, "op":conf[3][0], },
            { "layer":ConvLayer, "common":conv1,      "K":conf[3][1], },
        ) )
    else:
        partitions.append( (
            { "layer":PoolLayer, "common":pool3s1p1, "op":conf[3][0] },
        ) )
    return layer

networks = {
    "Alexnet" : (
        { "warmup":5 },
        { "layer":DataLayer, "N":128, "C":3, "H":224, "W":224},
        { "layer":ConvLayer, "common":conv11,"K":64,  "grid_P":55, "grid_Q":5, "update_size":"C128_K64"  },
        { "layer":PoolLayer, "common":pool3s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv5, "K":192, "grid_P":1,  "grid_Q":9, "update_size":"C128_K64"  },
        { "layer":PoolLayer, "common":pool3s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":384, "grid_P":13, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":13, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":13, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool3s2p0, "op":"max" },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":1000 },
    ),
    "Overfeat" : (
        { "warmup":1 },
        { "layer":DataLayer, "N":128, "C":3, "H":231, "W":231},
        { "layer":ConvLayer, "common":conv11p0,"K":96,   "grid_P":2, "grid_Q":8, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv5p0, "K":256, np.float16:{"grid_P":3, "grid_Q":3, "update_size":"C128_K64"}, np.float32:{"grid_P":2, "grid_Q":3, "update_size":"C128_K128"} },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3,   "K":512,  "grid_P":2, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3,   "K":1024, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3,   "K":1024, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":1000 },
    ),
    # See http://arxiv.org/pdf/1409.1556.pdf for variations
    "VGG" : (
        { "warmup":1 },
        { "layer":DataLayer, "N":64, "C":3, "H":224, "W":224},
        { "layer":ConvLayer, "common":conv3, "K":64, np.float16:{"grid_P":4, "grid_Q":224}, np.float32:{"grid_P":224, "grid_Q":4}, "update_size":"C128_K64" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":128, np.float16:{"grid_P":1, "grid_Q":28, "update_size":"C128_K128"}, np.float32:{"grid_P":1, "grid_Q":14, "update_size":"C128_K64"} },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":2, "grid_Q":8, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":1000 },
    ),
    # Here is the biggest VGG model (19 layers)
    "VGG_E" : (
        { "warmup":1 },
        { "layer":DataLayer, "N":64, "C":3, "H":224, "W":224},
        { "layer":ConvLayer, "common":conv3, "K":64,  np.float16:{"grid_P":4, "grid_Q":224}, np.float32:{"grid_P":224, "grid_Q":4}, "update_size":"C128_K64" },
        { "layer":ConvLayer, "common":conv3, "K":64,  np.float16:{"grid_P":2, "grid_Q":56 }, np.float32:{"grid_P":224, "grid_Q":4}, "update_size":"C128_K64" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":128, np.float16:{"grid_P":1, "grid_Q":28, "update_size":"C128_K128"}, np.float32:{"grid_P":1,   "grid_Q":14, "update_size":"C128_K64"} },
        { "layer":ConvLayer, "common":conv3, "K":128, np.float16:{"grid_P":1, "grid_Q":16, "update_size":"C128_K128"}, np.float32:{"grid_P":112, "grid_Q":4,  "update_size":"C128_K64"} },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":2, "grid_Q":8, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":256, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":4, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":ConvLayer, "common":conv3, "K":512, "grid_P":1, "grid_Q":1, "update_size":"C128_K128" },
        { "layer":PoolLayer, "common":pool2s2p0, "op":"max" },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":3072 },
        { "layer":FullLayer, "nOut":1000 },
    ),
    # http://arxiv.org/abs/1502.03167
    "GoogLeNet1" : (
        { "warmup":1 },
        { "layer":DataLayer, "N":128, "C":3, "H":224, "W":224 },
        { "layer":ConvLayer, "common":conv7, "K":64  },
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        { "layer":ConvLayer, "common":conv1, "K":64  },
        { "layer":ConvLayer, "common":conv3, "K":192 },
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        inception1( [(64, ),(96, 128),(16, 32),(32, )] ),
        inception1( [(128,),(128,192),(32, 96),(64, )] ),
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        inception1( [(192,),(96, 208),(16, 48),(64, )] ),
        inception1( [(160,),(112,224),(24, 64),(64, )] ),
        inception1( [(128,),(128,256),(24, 64),(64, )] ),
        inception1( [(112,),(144,288),(32, 64),(64, )] ),
        inception1( [(256,),(160,320),(32,128),(128,)] ),
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        inception1( [(256,),(160,320),(32,128),(128,)] ),
        inception1( [(384,),(192,384),(48,128),(128,)] ),
        { "layer":PoolLayer, "common":pool7s1p0, "op":"avg" },
        { "layer":FullLayer, "nOut":1000 },
    ),
    # adapted from: https://github.com/soumith/kaggle_retinopathy_starter.torch/blob/master/models/googlenet_cudnn.lua
    "GoogLeNet2" : (
        { "warmup":1 },
        { "layer":DataLayer, "N":128, "C":3, "H":224, "W":224 },
        { "layer":ConvLayer, "common":conv7, "K":64  },
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        { "layer":ConvLayer, "common":conv1, "K":64  },
        { "layer":ConvLayer, "common":conv3, "K":192 },
        { "layer":PoolLayer, "common":pool3s2p1, "op":"max" },
        inception2( [( 64,),( 64, 64),( 64, 96),('avg', 32)] ),
        inception2( [( 64,),( 64, 96),( 64, 96),('avg', 64)] ),
        inception2( [(  0,),(128,160),( 64, 96),('max',  0)] ),
        { "layer":ConvLayer, "common":conv2, "K":576 },
        inception2( [(224,),( 64, 96),( 96,128),('avg',128)] ),
        inception2( [(192,),( 96,128),( 96,128),('avg',128)] ),
        inception2( [(160,),(128,160),(128,160),('avg', 96)] ),
        inception2( [( 96,),(128,192),(160,192),('avg', 96)] ),
        inception2( [(  0,),(128,192),(192,256),('max',  0)] ),
        { "layer":ConvLayer, "common":conv2, "K":1024 },
        inception2( [(352,),(192,320),(160,224),('avg',128)] ),
        inception2( [(352,),(192,320),(192,224),('max',128)] ),
        { "layer":PoolLayer, "common":pool7s1p0, "op":"avg" },
        { "layer":FullLayer, "nOut":1000 },
    ),
}

for net in nets:

    for dtype in dtypes:

        warmup  = networks[net][0]["warmup"]
        network = networks[net][1:]
        name    = "%s (dtype=%s, N=%d)" % (net, np.dtype(dtype).name, network[0]["N"])

        print("------------------------------------------------")
        print("Benchmarking: " + name)
        print("------------------------------------------------")

        layers = []
        prev_layer = None
        max_deltas  = 0
        max_weights = 0
        max_delta_layer  = None
        max_weight_layer = None
        shared_weights   = None
        shared_deltas    = []
        inception        = False

        for conf in network:

            layer = Layer.create(ng, conf, prev_layer, dtype)

            if type(layer) is Inception:
                inception = True

            # find the size of the largest buffers so they can be shared
            if layer.sizeF > max_weights:
                max_weights = layer.sizeF
                max_weight_layer = layer

            if layer.sizeI > max_deltas and type(prev_layer) is not DataLayer:
                max_deltas = layer.sizeI
                max_delta_layer = layer

            prev_layer = layer
            layers.append(layer)

        # Init shared buffers (assumes consistent dtype for now)
        shared_deltas.append(ng.empty(max_delta_layer.dimI, dtype=max_delta_layer.dtype))
        shared_deltas.append(ng.empty(max_delta_layer.dimI, dtype=max_delta_layer.dtype))
        if inception:
            shared_deltas.append(ng.empty(max_delta_layer.dimI, dtype=max_delta_layer.dtype))
            shared_deltas.append(ng.empty(max_delta_layer.dimI, dtype=max_delta_layer.dtype))

        shared_updates = ng.empty(max_weight_layer.dimF, dtype=np.float32)

        for i, layer in enumerate(layers):
            if verbose:
                print(layer)

            # Intitalize buffers.  Alernate shared delta buffer.
            # One layer can't have the same buffer for both error in and error out.
            layer.init_activations()
            layer.init_weights(shared=shared_updates, zeros=zeros)
            if i > 1:
                layer.init_deltas(shared=shared_deltas)

        if verbose:
            remain, total = drv.mem_get_info()
            print("%.3fGB of %.3fGB Allocated (%.3fGB Remaining)" %
                  ((total-remain)/1024.**3, total/1024.**3, remain/1024.**3))

        if zeros:
            layers[0].init_data()
        else:
            # give the first layer some data
            layers[0].init_data(np.random.uniform(0.0, 1.0, layers[0].dimO))

            # Scale the initial weights so activations are bound around 1.0
            # We do this by running it through the forward pass and collecting mean stats
            ng.bench = False
            propagation = None
            for layer in layers:
                propagation = layer.fprop(propagation, scale_weights=.5)
            ng.bench = layer_bench

        start = drv.Event()
        end   = drv.Event()

        fprop_time  = 0
        bprop_time  = 0
        fprop_flops = 0
        bprop_flops = 0

        # We throw away the first two runs as it includes pycuda kernel loading times and clock warmup.
        # So add 1 to our loop count.
        for loop in range(loops+warmup):

            loop = loop - warmup + 1
            if loop < 0: loop = 0

            start.record()
            flops = 0

            #fprop
            propagation = None
            for layer in layers:

                propagation = layer.fprop(propagation)

                flops += layer.flops
                if print_stats:
                    layer.fprop_stats()

            end.record()
            end.synchronize()
            msecs = end.time_since(start)
            print("fprop(%2d): %8.3f msecs %8.3f gflops" %
                  (loop, msecs, flops / (msecs * 1000000.0)))
            if loop > 0:
                fprop_time  += msecs
                fprop_flops += flops

            start.record()
            flops = 0

            #bprop
            for layer in layers[:0:-1]:

                propagation = layer.bprop(propagation)

                flops += layer.flops * 2
                if print_stats:
                    layer.bprop_stats()

            end.record()
            end.synchronize()
            msecs = end.time_since(start)
            print("bprop(%2d): %8.3f msecs %8.3f gflops" %
                  (loop, msecs, flops / (msecs * 1000000.0)))
            if loop > 0:
                bprop_time  += msecs
                bprop_flops += flops

        if loops > 0:

            print("---------------------------------------------")
            print(name + " Results:")
            print("---------------------------------------------")
            print("Avg(%d) fprop: %8.3f msecs %.3f gflops" %
                  (loops, fprop_time/loops, fprop_flops / (fprop_time * 1000000.0)))

            print("Avg(%d) bprop: %8.3f msecs %.3f gflops" %
                  (loops, bprop_time/loops, bprop_flops / (bprop_time * 1000000.0)))

            fprop_time  += bprop_time
            fprop_flops += bprop_flops

            print("Avg(%d) total: %8.3f msecs %.3f gflops\n\n" %
                  (loops, fprop_time/loops, fprop_flops / (fprop_time * 1000000.0)))
