#!/bin/sh
sed -i '1i\SERIAL 0 115200' "$1/boot/isolinux/isolinux.cfg"