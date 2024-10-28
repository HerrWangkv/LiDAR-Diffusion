# Data Preparation
## KITTI

```sh
mkdir kitti_semantic_360
cd kitti_semantic_360
wget https://s3.eu-central-1.amazonaws.com/avg-projects/KITTI-360/a1d81d9f7fc7195c937f9ad12e2a2c66441ecb4e/download_3d_velodyne.zip
unzip download_3d_velodyne.zip
./download_3d_velodyne.sh
wget https://s3.eu-central-1.amazonaws.com/avg-projects/KITTI-360/a1d81d9f7fc7195c937f9ad12e2a2c66441ecb4e/download_2d_perspective.zip
unzip download_2d_perspective.zip
./download_2d_perspective.sh
rm download*

mkdir SemanticKITTI
cd SemanticKITTI
wget https://s3.eu-central-1.amazonaws.com/avg-kitti/data_odometry_velodyne.zip
unzip data_odometry_velodyne.zip
wget https://s3.eu-central-1.amazonaws.com/avg-kitti/data_odometry_calib.zip
unzip data_odometry_calib.zip
wget https://www.semantic-kitti.org/assets/data_odometry_labels.zip
unzip data_odometry_labels.zip
```