FROM nvidia/cuda:10.0-cudnn7-devel-ubuntu16.04

LABEL maintainer "Gemfield <gemfield@civilnet.cn>"

## basic
RUN mv /etc/apt/sources.list.d/cuda.list /etc/apt/sources.list.d/cuda.list.gemfield &&\
        apt-get update && apt-get install -y --no-install-recommends \
        libgoogle-glog-dev \
        libgtest-dev \
        libiomp-dev \
        liblapack-dev \
        libleveldb-dev \
        liblmdb-dev \
        libopencv-dev \
        libopenmpi-dev \
        libsnappy-dev \
        libprotobuf-dev \
        openmpi-bin \
        openmpi-doc \
        protobuf-compiler \
        python-dev \
        python-pip \
        build-essential \
        cmake \
        git \
        ffmpeg \
        python-yaml \
        python-setuptools && \
    rm -rf /var/lib/apt/lists/*

## python
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pip -U
RUN hash pip && pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir kafka-python redis==2.10.6 future opencv-python numpy protobuf networkx==2.2 scipy==1.1.0
RUN pip install --no-cache-dir confluent-kafka

## caffe2, non-local-block
WORKDIR /opt/
COPY video_nonlocal_net_caffe2 caffe2

RUN cd caffe2/ && mkdir build && cd build && cmake .. && make -j4
WORKDIR /opt/caffe2/
RUN rm -rf .git && python setup_caffe2.py install && find . -name "*.o" -exec rm -f {} \+
ENV PYTHONPATH=/opt/caffe2/video-nonlocal-net/lib
# Commands when creating a new container
WORKDIR /bigdata/gemfield/soccer/3d/
