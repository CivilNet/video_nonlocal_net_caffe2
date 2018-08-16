/**
 * Copyright (c) 2017-present, Facebook, Inc.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */
 /**
  * based on:
  * Copyright (c) 2016-present, Facebook, Inc.
  *
  * Licensed under the Apache License, Version 2.0 (the "License");
  * you may not use this file except in compliance with the License.
  * You may obtain a copy of the License at
  *
  *     http://www.apache.org/licenses/LICENSE-2.0
  *
  * Unless required by applicable law or agreed to in writing, software
  * distributed under the License is distributed on an "AS IS" BASIS,
  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  * See the License for the specific language governing permissions and
  * limitations under the License.
  */
  
#ifndef CUSTOMIZED_VIDEO_IO_H_
#define CUSTOMIZED_VIDEO_IO_H_

#include <opencv2/opencv.hpp>
#include <random>
#include "caffe/proto/caffe.pb.h"

#include <iostream>

namespace caffe2 {

void ImageChannelToBuffer(const cv::Mat* img, float* buffer, int c);

void ImageDataToBuffer(
    unsigned char* data_buffer,
    int height,
    int width,
    float* buffer,
    int c);

int GetNumberOfFrames(std::string filename);

double GetVideoFPS(std::string filename);

void GetVideoMeta(std::string filename, int& number_of_frames, double& fps);

bool ReadClipFromFrames(
    std::string input_dir,
    const int start_frm,
    std::string file_extension,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer);

bool ReadClipFromVideoLazzy(
    std::string filename,
    const int start_frm,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer);

bool ReadClipFromVideoSequential(
    std::string filename,
    const int start_frm,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer);

bool ReadClipFromVideo(
    std::string filename,
    const int start_frm,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer);

bool DecodeClipFromVideoFile(
    std::string filename,
    const int start_frm,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer);

bool DecodeClipFromMemoryBuffer(
    const char* video_buffer,
    const int size,
    const int start_frm,
    const int length,
    const int height,
    const int width,
    const int sampling_rate,
    float*& buffer,
    std::mt19937* randgen);

// ----------------------------------------------------------------
// customized functions follow
// ----------------------------------------------------------------

void ClipTransformFlex(
    const float* clip_data,
    const int channels,
    const int length,
    const int height,
    const int width,
    const int h_crop,
    const int w_crop,
    const bool mirror,
    float mean,
    float std,
    float* transformed_clip,
    std::mt19937* randgen,
    std::bernoulli_distribution* mirror_this_clip,
    const bool use_center_crop,
    const bool use_bgr,
    const int spatial_pos
  );

void ScaleTransform(
    const float* clip_data,
    const int channels,
    const int length,
    const int height,
    const int width,
    const int max_size,
    const int min_size,
    float*& buffer,
    std::mt19937* randgen,
    int & new_height,
    int & new_width);

bool DecodeClipFromVideoFileFlex(
    std::string filename,
    const int start_frm,
    const int length,
    int & height,
    int & width,
    const int sampling_rate,
    float*& buffer,
    std::mt19937* randgen,
    const int sample_times);

bool DecodeClipFromMemoryBufferFlex(
    const char* video_buffer,
    const int size,
    const int start_frm,
    const int length,
    int & height,
    int & width,
    const int sampling_rate,
    float*& buffer,
    std::mt19937* randgen);
}



#endif // CUSTOMIZED_VIDEO_IO_H_
