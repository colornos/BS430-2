import logging
import sys
import time
from configparser import ConfigParser
from struct import pack

import pygatt

# Load the functions from the original script
def init_ble_mode():
    global ble_mode
    ble_mode = 'Indication'

    log.info('BLE mode set to: %s' % ble_mode)
    return True

def wait_for_device(device_name):
    log.info('Waiting for device %s to become available...' % device_name)
    while True:
        devices = adapter.scan(run_as_root=True, timeout=3)
        for dev in devices:
            if dev['name'] == device_name:
                log.info('Found %s' % device_name)
                return
        time.sleep(3)

 def connect_device(ble_address):
    global device
    device = None
    while device is None:
        try:
            device = adapter.connect(ble_address, address_type=addresstype)
            log.info('Connected to device %s' % ble_address)
        except pygatt.exceptions.NotConnectedError:
            log.warning('Failed to connect, retrying...')
            time.sleep(1)
    return device

# Read .ini file and set plugins-folder
config = ConfigParser()
config.read('BS430.ini')
path = "plugins/"

# Set up logging
numeric_level = getattr(logging, config.get('Program', 'loglevel').upper(), None)
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

# Load scale information from .ini-file
ble_address = config.get('Scale', 'ble_address')
device_name = config.get('Scale', 'device_name')
device_model = config.get('Scale', 'device_model')

if device_model == 'BS410':
    addresstype = pygatt.BLEAddressType.public
    time_offset = 1262304000
elif device_model == 'BS444':
    addresstype = pygatt.BLEAddressType.public
    time_offset = 1262304000
else:
    addresstype = pygatt.BLEAddressType.random
    time_offset = 0

# Global variable to store the last weight timestamp
last_weight_timestamp = 0

def processIndication(handle, data):
    global weightdata, last_weight_timestamp

    if handle == handle_weight:
        weight = decodeWeightData(data)
        if weight['timestamp'] > last_weight_timestamp:
            last_weight_timestamp = weight['timestamp']
            weightdata = [weight]  # Replace weightdata with the new weight

log.info('BS440 Started')
if not init_ble_mode():
    sys.exit()

adapter = pygatt.backends.GATTToolBackend()
adapter.start()

while True:
    wait_for_device(device_name)
    device = connect_device(ble_address)
    if device:
        weightdata = []
        try:
            handle_weight = device.get_handle(Char_weight)
            continue_comms = True
        except pygatt.exceptions.NotConnectedError:
            log.warning('Error getting handles')
            continue_comms = False

        log.info('Continue Comms: ' + str(continue_comms))
        if (not continue_comms): continue

        try:
            device.subscribe(Char_weight,
                             callback=processIndication,
                             indication=True)
        except pygatt.exceptions.NotConnectedError:
            continue_comms = False

        if continue_comms:
            timestamp = bytearray(pack('<I', int(time.time() - time_offset)))
            timestamp.insert(0, 2)
            try:
                device.char_write_handle(handle_command, timestamp,
                                         wait_for_response=True)
            except pygatt.exceptions.NotificationTimeout:
                pass
            except pygatt.exceptions.NotConnectedError:
                continue_comms = False
            if continue_comms:
                log.info('Waiting for notifications for another 30 seconds')
                time.sleep(30)
                try:
                    device.disconnect()
                except pygatt.exceptions.NotConnectedError:
                    log.info('Could not disconnect...')

                log.info('Done receiving data from scale')

                # Process the most recent weight data
                if weightdata:
                    log.info(f"Most recent weight data: {weightdata[0]}")
                else:
                    log.error('Unreliable data received. Unable to process')
