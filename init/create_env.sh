#!/usr/bin/bash

# install rust compiler
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
export PATH="$HOME/.cargo/bin:$PATH"

# create conda environment
conda create -n lidar_diffusion python=3.10.11 -y
conda activate lidar_diffusion

# install dependencies
pip install --upgrade pip
pip install torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cu118
pip install torchmetrics==0.5.0 pytorch-lightning==1.4.2 omegaconf==2.1.1 einops==0.3.0 transformers==4.36.2 imageio==2.9.0 imageio-ffmpeg==0.4.2 opencv-python kornia==0.7.0
pip install gdown scipy pyyaml joblib easydict wandb more_itertools
pip install -e git+https://github.com/CompVis/taming-transformers.git@master#egg=taming-transformers
pip install -e git+https://github.com/openai/CLIP.git@main#egg=clip

# install Google Sparse Hash library
cd ..
git clone https://github.com/sparsehash/sparsehash.git
cd sparsehash
./configure --prefix=/home/kwang/opt
make
make install

# install torchsparse (optional)
export PATH=/usr/local/cuda-11.8/bin:$PATH
export CPLUS_INCLUDE_PATH=/home/kwang/opt/include
pip install git+https://github.com/mit-han-lab/torchsparse.git@v1.4.0
