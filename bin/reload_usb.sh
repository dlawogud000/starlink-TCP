echo 2-1:1.0 | sudo tee /sys/bus/usb/drivers/r8152/unbind
sleep 2
echo 2-1:1.0 | sudo tee /sys/bus/usb/drivers/r8152/bind
sleep 10
sudo ip route add default via 192.168.1.1 dev enx588694fda289 table 100

# ls /sys/bus/usb/devices
# readlink -f /sys/class/net/enx588694fda289/device