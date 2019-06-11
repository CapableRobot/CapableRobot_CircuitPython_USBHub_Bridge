# The MIT License (MIT)
#
# Copyright (c) 2019 Chris Osterwood for Capable Robot Components
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import os
import glob
import yaml
import struct
import time
import logging
import copy

import usb.core
import usb.util

from .registers import registers
from .i2c import USBHubI2C
from .util import *


ADDR_USC12 = 0x57
ADDR_USC34 = 0x56

UCS2113_PORT1_CURRENT = 0x00
UCS2113_PORT2_CURRENT = 0x01
UCS2113_PORT_STATUS   = 0x02
UCS2113_INTERRUPT1    = 0x03
UCS2113_INTERRUPT2    = 0x04
UCS2113_CURRENT_LIMIT = 0x14

UCS2113_CURRENT_MAP = [
    530,
    960,
    1070,
    1280,
    1600,
    2130,
    2670,
    3200
]

PORT_MAP = ["port2", "port4", "port1", "port3"]

REGISTER_NEEDS_PORT_REMAP = [
    'port::connection',
    'port::device_speed'
]

REQ_OUT = usb.util.build_request_type(
    usb.util.CTRL_OUT,
    usb.util.CTRL_TYPE_VENDOR,
    usb.util.CTRL_RECIPIENT_DEVICE)

REQ_IN = usb.util.build_request_type(
    usb.util.CTRL_IN,
    usb.util.CTRL_TYPE_VENDOR,
    usb.util.CTRL_RECIPIENT_DEVICE)

def register_keys(parsed, sort=True):
    if sort:
        keys = sorted(parsed.body.keys())
    else:
        parsed.body.keys()

    ## Remove any key which starts with 'reserved' or '_'
    return list(filter(lambda key: key[0] != '_' and ~key.startswith("reserved") , keys))

def set_bit(value, bit):
    return value | (1<<bit)

def clear_bit(value, bit):
    return value & ~(1<<bit)

def get_bit(value, bit):
    return (value & (1<<bit)) > 0 


class USBHub:

    CMD_REG_WRITE = 0x03
    CMD_REG_READ  = 0x04
    CMD_I2C_ENTER = 0x70
    CMD_I2C_WRITE = 0x71
    CMD_I2C_READ  = 0x72

    ID_PRODUCT = 0x494C
    ID_VENDOR  = 0x0424

    REG_BASE_DFT = 0xBF800000
    REG_BASE_ALT = 0xBFD20000

    TIMEOUT = 10000

    def __init__(self, vendor=None, product=None):
        if vendor == None:
            vendor = self.ID_VENDOR
        if product == None:
            product = self.ID_PRODUCT

        self.attach(vendor, product)

        this_dir = os.path.dirname(os.path.abspath(__file__))
        self.definition = {}

        for file in glob.glob("%s/../formats/*.ksy" % this_dir):
            key = os.path.basename(file).replace(".ksy","")
            self.definition[key] = yaml.load(open(file), Loader=yaml.SafeLoader)

        # Extract the dictionary of register addresses to names
        # Flip the keys and values (name will now be key)
        # Add number of bytes to the mapping table, extracted from the YAML file
        #
        # Register names (keys) have the 'DEVICE_' prefix removed from them
        # but still have the '::' and '_' seperators
        mapping = self.definition['usb4715']['types']['register']['seq'][-1]['type']['cases']
        mapping = {v:k for k,v in mapping.items()}
        self.mapping = {k.replace('usb4715_',''):[v,self.get_register_length(k),self.get_register_endian(k)] for k,v in mapping.items()}

    # Function to extract and sum the number of bits in each register defintion.
    # For this to function correctly, all lengths MUST be in bit lengths
    # Key is split into namespace to correctly locate the right sequence field
    def get_register_length(self, key):
        key = key.split("::")
        seq = self.definition[key[0]]['types'][key[1]]['seq']
        return sum([int(v['type'].replace('b','')) for v in seq])

    def get_register_endian(self, key):
        key = key.split("::")
        obj = self.definition[key[0]]['types'][key[1]]

        if 'meta' in obj:
            if 'endian' in obj['meta']:
                value = obj['meta']['endian']
                if value == 'le':
                    return 'little'

        return 'big'

    def find_name(self, register):
        for name, value in self.mapping.items():
            if value[0] == register:
                return name

        raise ValueError("Unknown register address : %s" % hex(register))

    def find(self, name):

        if name in self.mapping:
            register, bits, endian = self.mapping[name]

            if bits in [8, 16, 24, 32]:
                return register, bits, endian
            else:
                raise ValueError("Register %s has %d bits" % (name, bits))
        else:
            raise ValueError("Unknown register name : %s" % name)

    def attach(self, vendor=ID_VENDOR, product=ID_PRODUCT):
        self.dev = usb.core.find(idVendor=vendor, idProduct=product)

        if self.dev  is None:
            raise ValueError('Device not found')

        cfg = self.dev.get_active_configuration()
        interface = cfg[(2,0)]
        self.out_ep, self.in_ep = sorted([ep.bEndpointAddress for ep in interface])

        self.i2c = USBHubI2C(self.dev)

    def register_read(self, name=None, addr=None, length=1, print=False):
        if name != None:
            address, bits, endian = self.find(name)
            addr = address + self.REG_BASE_DFT
            length = int(bits / 8)
        else:
            name = self.find_name(addr)

        if addr == None:
            raise ValueError('Must specify an name or address')

        logging.info("-- register {} ({}) read {} -- ".format(name, hexstr(addr), length))

        ## Split 32 bit register address into the 16 bit value & index fields
        value = addr & 0xFFFF
        index = addr >> 16

        data = list(self.dev.ctrl_transfer(REQ_IN, self.CMD_REG_READ, value, index, length))

        if length != len(data):
            raise ValueError('Incorrect data length')

        shift = 0

        if bits == 8:
            code = 'B'
        elif bits == 16:
            code = 'H'
        elif bits == 24:
            ## There is no good way to extract a 3 byte number.
            ##
            ## So we tell pack it's a 4 byte number and shift all the data over 1 byte
            ## so it decodes correctly (as the register defn starts from the MSB)
            code = 'L'
            shift = 8
        elif bits == 32:
            code = 'L'

        num    = bits_to_bytes(bits)
        value  = int_from_bytes(data, endian)
        stream = struct.pack(">HB" + code, *[address, num, value << shift])
        parsed = self.parse_register(name, stream)

        if print:
            self.print_register(parsed)

        data.reverse()
        logging.info(" ".join([hexstr(v) for v in data]))
        return data, parsed

    def print_register(self, data):
        meta = {}
        body = data.body

        # Allows for printing of KaiTai and Construct objects
        # Construct containers already inherit from dict, but
        # KaiTai objects need to be converted via vars call
        if not isinstance(body, dict):
            body = vars(data.body)

        for key, value in body.items():
            if key.startswith("reserved") or key[0] == "_":
                continue

            meta[key] = value

        addr = hex(data.addr).upper().replace("0X","0x")
        # name = type(data.body).__name__

        name = self.find_name(data.addr)

        print("%s %s" % (addr, name) )
        for key in sorted(meta.keys()):
            value = meta[key]
            print("       %s : %s" % (key, hex(value)))

    def parse_register(self, name, stream):
        parsed = registers.parse(stream)[0]

        if name in REGISTER_NEEDS_PORT_REMAP:
            raw = copy.deepcopy(parsed)

            for key, value in raw.body.items():
                if key in PORT_MAP:
                    port = PORT_MAP[int(key.replace("port",""))-1]
                    parsed.body[port] = value

        return parsed

    def currents(self, ports=[1,2,3,4]):
        TO_MA = 13.3

        out = []

        for port in ports:
            if port == 1 or port == 2:
                i2c_addr = ADDR_USC12
            else:
                i2c_addr = ADDR_USC34

            if port == 1 or port == 3:
                reg_addr = UCS2113_PORT1_CURRENT
            else:
                reg_addr = UCS2113_PORT2_CURRENT

            value = self.i2c.read_i2c_block_data(i2c_addr, reg_addr)[0]
            out.append(float(value) * TO_MA)

        return out

    def current_limits(self):
        out = []
        reg_addr = UCS2113_CURRENT_LIMIT

        for i2c_addr in [ADDR_USC12, ADDR_USC34]:
            value = self.i2c.read_i2c_block_data(i2c_addr, reg_addr)[0]

            ## Extract Port 1 of this chip
            out.append(value & 0b111)

            ## Extract Port 2 of this chip
            out.append((value >> 3) & 0b111)

        return [UCS2113_CURRENT_MAP[key] for key in out]

    def current_alerts(self):
        out = []

        for idx, i2c_addr in enumerate([ADDR_USC12, ADDR_USC34]):

            value = self.i2c.read_i2c_block_data(i2c_addr, UCS2113_PORT_STATUS)[0]

            if get_bit(value, 7):
                out.append("ALERT.{}".format(idx*2+1))

            if get_bit(value, 6):
                out.append("ALERT.{}".format(idx*2+2))

            if get_bit(value, 5):
                out.append("CC_MODE.{}".format(idx*2+1))

            if get_bit(value, 4):
                out.append("CC_MODE.{}".format(idx*2+2))


            value = self.i2c.read_i2c_block_data(i2c_addr, UCS2113_INTERRUPT1)[0]

            if get_bit(value, 7):
                out.append("ERROR.{}".format(idx*2+1))

            if get_bit(value, 6):
                out.append("DISCHARGE.{}".format(idx*2+1))

            if get_bit(value, 5):
                if idx == 0:
                    out.append("RESET.12")
                else:
                    out.append("RESET.34")

            if get_bit(value, 4):
                out.append("KEEP_OUT.{}".format(idx*2+1))

            if get_bit(value, 3):
                if idx == 0:
                    out.append("DIE_TEMP_HIGH.12")
                else:
                    out.append("DIE_TEMP_HIGH.34")

            if get_bit(value, 2):
                if idx == 0:
                    out.append("OVER_VOLT.12")
                else:
                    out.append("OVER_VOLT.34")

            if get_bit(value, 1):
                out.append("BACK_BIAS.{}".format(idx*2+1))

            if get_bit(value, 0):
                out.append("OVER_LIMIT.{}".format(idx*2+1))


            value = self.i2c.read_i2c_block_data(i2c_addr, UCS2113_INTERRUPT2)[0]

            if get_bit(value, 7):
                out.append("ERROR.{}".format(idx*2+2))

            if get_bit(value, 6):
                out.append("DISCHARGE.{}".format(idx*2+2))

            if get_bit(value, 5):
                if idx == 0:
                    out.append("VS_LOW.12")
                else:
                    out.append("VS_LOW.34")

            if get_bit(value, 4):
                out.append("KEEP_OUT.{}".format(idx*2+2))

            if get_bit(value, 3):
                if idx == 0:
                    out.append("DIE_TEMP_LOW.12")
                else:
                    out.append("DIE_TEMP_LOW.34")

            ## Bit 2 is unimplemented

            if get_bit(value, 1):
                out.append("BACK_BIAS.{}".format(idx*2+2))

            if get_bit(value, 0):
                out.append("OVER_LIMIT.{}".format(idx*2+2))

        return out

    def connections(self):
        _, conn = self.register_read(name='port::connection')
        return [conn.body[key] == 1 for key in register_keys(conn)]

    def speeds(self):
        _, speed = self.register_read(name='port::device_speed')
        speeds = ['none', 'low', 'full', 'high']
        return [speeds[speed.body[key]] for key in register_keys(speed)]