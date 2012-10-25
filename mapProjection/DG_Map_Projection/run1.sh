#!/bin/sh

if [ "$#" -lt 3 ]; then 
    echo Usage: $0 session ls tag
    exit
fi

s=$1
ls=$2 # or nolsq
tag=$3

export PATH=$HOME/projects/StereoPipeline/src/asp/Tools:$PATH

t=$s"_"$ls"_"$tag

in1=WV01_11JUN171531433-P1BS-102001001549B500
in2=WV01_11JUN171532408-P1BS-1020010014597A00
opt="+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
dem=gimpdem_90m.tiled.tif

mpp=1
gdt=$HOME/projects/base_system/bin/gdal_translate

rm -rfv res$t; mkdir res$t

# Stereo of non-map-projected images
std=stereo_homography"_"$ls.default
time stereo --threads=16 -s $std -t $s $in1.tif $in2.tif $in1.xml $in2.xml res$t/noproj # $dem

point2dem --nodata-value 0 res$t/noproj-PC.tif --orthoimage res$t/noproj-L.tif
ls -l res$t/noproj-DRG.tif res$t/noproj-DEM.tif res$t/noproj-PC.tif
show_dems.pl res$t/noproj-DEM.tif
