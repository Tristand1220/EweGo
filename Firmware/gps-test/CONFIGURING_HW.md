# Configuring the ZED-X20P modules

0.) Install UCenter2 (sadly, Windows only)

1.) Connect module via a USB-Serial connector

2.) In UCenter2 - click on the "+" to connect to a new module. The software will cycle through baud rates to find the right one.

3.) Revert the module to default settings. Note that this will make baud rate 38400.

4.) Update the baud rate to our setting.

a.) Click on the gear to configure the module. 
b.) Find CFG-UART1, and select "Rate"
c.) Enter 460800 into the rate box. 
d.) Check the "RAM" box and click "Set".
e.) Click "Send" to upload to module. It will claim to fail, but it actually succeeds.
f.) Exit the configuration menu, and change the serial baud rate to 460800.
g.) Repeat the steps to change the rate, except now make sure to check both "RAM" and "Flash".
h.) Click "Set" and then "Send", and watch that both the RAM and Flash setting messages get a green checkmark.


5.) Make the other settings.

a.) Import the "SheepRTK.ucf" settings file. Click on "Send" to make all these settings. (Watch for the green checkmarks.)

b.) Import the "TimePulseConfiguration.ucf" file. Click on "Send" to make all these settings. (Watch for the green checkmarks.)


You should now be good to run the gps_logger.py program when connected to the module.
