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

# Relevant characteristics submitted by the scale
Char_person = '00008a82-0000-1000-8000-00805f9b34fb'  # person data
Char_weight = '00008a21-0000-1000-8000-00805f9b34fb'  # weight data
Char_command = '00008a81-0000-1000-8000-00805f9b34fb'  # command register
# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def decodePerson(handle, values):

    data = unpack('BxBxBBBxB', bytes(values[0:9]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x84)
    retDict["person"] = data[1]
    if data[2] == 1:
        retDict["gender"] = "male"
    else:
        retDict["gender"] = "female"
    retDict["age"] = data[3]
    retDict["size"] = data[4]
    if data[5] == 3:
        retDict["activity"] = "high"
    else:
        retDict["activity"] = "normal"
    return retDict

def decodeWeight(handle, values):

    data = unpack('<BHxxIxxxxB', bytes(values[0:14]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x1d)
    retDict["weight"] = data[1]/100.0
    retDict["timestamp"] = sanitize_timestamp(data[2])
    retDict["person"] = data[3]
    return retDict

def sanitize_timestamp(timestamp):

    retTS = 0
    if timestamp + time_offset < sys.maxsize:
        retTS = timestamp + time_offset
    else:
        retTS = timestamp
    if timestamp >= sys.maxsize:
        retTS = 0
    return retTS

def processIndication(handle, values):

    if handle == handle_person:
        result = decodePerson(handle, values)
        if result not in persondata:
            log.info(str(result))
            persondata.append(result)
        else:
            log.info('Duplicate persondata record')
    elif handle == handle_weight:
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
            found = adapter.filtered_scan(devname)
        except pygatt.exceptions.BLEError:
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
Start BLE comms and run that forever
'''
log.info('BS440 Started')
if not init_ble_mode():
    sys.exit()

adapter = pygatt.backends.GATTToolBackend()
adapter.start()

while True:
    wait_for_device(device_name)
    device = connect_device(ble_address)
    # If the device was connected successfully (the variable "device" has
    # been defined an contains the instance of the BLEDevice) the main loop runs
    if device:
        persondata = []
        weightdata = []
        try:
            # Get the two-byte shortcut (the handle)
            handle_person = device.get_handle(Char_person)
            handle_weight = device.get_handle(Char_weight)
            handle_command = device.get_handle(Char_command)
            continue_comms = True
        except pygatt.exceptions.NotConnectedError:
            log.warning('Error getting handles')
            continue_comms = False

        log.info('Continue Comms: ' + str(continue_comms))
        if (not continue_comms): continue

        '''
        subscribe to characteristics and have processIndication
        process the data received.
        '''
        try:
            device.subscribe(Char_weight,
                             callback=processIndication,
                             indication=True)
            device.subscribe(Char_person,
                             callback=processIndication,
                             indication=True)
        except pygatt.exceptions.NotConnectedError:
            continue_comms = False

        '''
        Send the unix timestamp in little endian order preceded by 02 as
        bytearray to handle 0x23. This will resync the scale's RTC.
        While waiting for a response notification, which will never
        arrive, the scale will emit 30 Indications on 0x1b (weight)
        and 0x1e (body) each.
        '''
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
                # process data if all received well
                if persondata and weightdata:
                    # Sort scale output by timestamp to retrieve most recent three results
                    weightdatasorted = sorted(weightdata, key=lambda k: k['timestamp'], reverse=True)
                    
                    # Run all plugins found
                    for plugin in plugins.values():
                        plugin.execute(config, persondata, weightdatasorted)
                else:
                    log.error('Unreliable data received. Unable to process')
