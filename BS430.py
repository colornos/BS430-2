import multiprocessing
import sys
import pygatt.backends
import logging
from configparser import ConfigParser
import time
import subprocess
from struct import *
from binascii import hexlify
import os
import threading
from time import sleep
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

GPIO.setwarnings(False)

# Interesting characteristics
Char_weight = '00008a21-0000-1000-8000-00805f9b34fb'  # weight data
Char_command = '00008a81-0000-1000-8000-00805f9b34fb'  # command register

def sanitize_timestamp(timestamp):
    retTS = time.time()
    return retTS

def decodeWeight(handle, values):
    data = unpack('<BHxxIxxxxB', bytes(values[0:14]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x1d)
    # Weight is reported in 10g. Hence, divide by 100.0
    # To force results to be floats: devide by float.
    retDict["weight"] = data[1] / 100.0
    retDict["timestamp"] = sanitize_timestamp(data[2])
    retDict["person"] = data[3]
    return retDict

def processIndication(handle, values):
    if handle == handle_weight:
        result = decodeWeight(handle, values)
        if result not in weightdata:
            log.info(str(result))
            weightdata.append(result)
        else:
            log.info('Duplicate weightdata record')
    else:
        log.debug('Unhandled Indication encountered')

def wait_for_device(devname):
    found = False
    while not found:
        try:
            # wait for bpm to wake up and connect to it
            found = adapter.filtered_scan(devname)
        except pygatt.exceptions.BLEError:
            # reset adapter when (see issue #33)
            adapter.reset()
    return

def connect_device(address):
    device_connected = False
    tries = 3
    device = None
    while not device_connected and tries > 0:
        try:
            device = adapter.connect(address, 8, addresstype)
            device_connected = True
        except pygatt.exceptions.NotConnectedError:
            tries -= 1
    return device

def init_ble_mode():
    p = subprocess.Popen("sudo btmgmt le on", stdout=subprocess.PIPE,
                         shell=True)
    (output, err) = p.communicate()
    if not err:
        log.info(output)
        return True
    else:
        log.info(err)
        return False

'''
Main program loop
'''

config = ConfigParser()
config.read('BS430.ini')
path = "plugins/"
plugins = {}

# set up logging
numeric_level = getattr(logging,
                        config.get('Program', 'loglevel').upper(),
                        None)
if not isinstance(numeric_level, int):
    raise ValueError('Invalid log level: %s' % loglevel)
logging.basicConfig(level=numeric_level,
                    format='%(asctime)s %(levelname)-8s %(funcName)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=config.get('Program', 'logfile'),
                    filemode='w')
log = logging.getLogger(__name__)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(numeric_level)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(funcName)s %(message)s')
ch.setFormatter(formatter)
log.addHandler(ch)

# Load configured plugins

if config.has_option('Program', 'plugins'):
    config_plugins = config.get('Program', 'plugins').split(',')
    config_plugins = [plugin.strip(' ') for plugin in config_plugins]
    log.info('Configured plugins: %s' % ', '.join(config_plugins))

    sys.path.insert(0, path)
    for plugin in config_plugins:
        log.info('Loading plugin: %s' % plugin)
        mod = __import__(plugin)
        plugins[plugin] = mod.Plugin()
    log.info('All plugins loaded.')
else:
    log.info('No plugins configured.')
sys.path.pop(0)

ble_address = config.get('Scale', 'ble_address')
device_name = config.get('Scale', 'device_name')
device_model = config.get('Scale', 'device_model')

if device_model == '444':
    addresstype = pygatt.BLEAddressType.public
    # On BS410 time=0 equals 1/1/2010. 
    # time_offset is used to convert to unix standard
    time_offset = 0
else:
    addresstype = pygatt.BLEAddressType.random
    time_offset = 0
'''
Start BLE comms and run that forever
'''
log.info('BS430 Started')
if not init_ble_mode():
    sys.exit()

adapter = pygatt.backends.GATTToolBackend()
adapter.start()

while True:
    wait_for_device(device_name)
    device = connect_device(ble_address)
    if device:
        weightdata = []
        handle_weight = device.get_handle(Char_weight)
        continue_comms = True

        try:
            device.subscribe(Char_weight,
                             callback=processIndication,
                             indication=True)
        except pygatt.exceptions.NotConnectedError:
            continue_comms = False

        if continue_comms:
            log.info('Waiting for notifications for another 30 seconds')
            time.sleep(30)
            try:
                device.disconnect()
            except pygatt.exceptions.NotConnectedError:
                log.info('Could not disconnect...')

            log.info('Done receiving data from the scale')
            if weightdata:
                weightdatasorted = sorted(weightdata, key=lambda k: k['timestamp'], reverse=True)

                for plugin in plugins.values():
                    plugin.execute(config, weightdatasorted)
            else:
                log.error('Unreliable data received. Unable to process')
