echo 2-5:1.0 | sudo tee /sys/bus/usb/drivers/r8152/unbind
sleep 2
echo 2-5:1.0 | sudo tee /sys/bus/usb/drivers/r8152/bind
sleep 10
sudo ip route add default via 192.168.1.1 dev enx588694fda289 table 100