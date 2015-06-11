# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring, invalid-name
##############################################################################
#
# Copyright (c) 2011, Martín Raúl Villalba
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#
##############################################################################

from __future__ import division, absolute_import, print_function, unicode_literals

from uuid import uuid4
from threading import Lock

from ant.core import event, message
from ant.core.constants import (RESPONSE_NO_ERROR, EVENT_CHANNEL_CLOSED,
                                CHANNEL_TYPE_TWOWAY_RECEIVE, MESSAGE_CAPABILITIES)
from ant.core.exceptions import ChannelError, MessageError, NodeError
from ant.core.message import ChannelMessage


class Network(object):
    def __init__(self, key=b'\x00' * 8, name=None):
        self.key = key
        self.name = name
        self.number = 0
    
    def __str__(self):
        name = self.name
        return name if name is not None else self.key


class Device(object):
    def __init__(self, devNumber, devType, transmissionType):
        self.number = devNumber
        self.type = devType
        self.transmissionType = transmissionType


class Channel(event.EventCallback):
    def __init__(self, node, number=0):
        self.node = node
        self.name = str(uuid4())
        self.number = number
        self.callbacks = set()
        self.evmCallbackLock = Lock()
        self.type = CHANNEL_TYPE_TWOWAY_RECEIVE
        self.network = None
        self.device = None
        self._searchTimeout = None
        self._period = None
        self._frequency = None
    
    def assign(self, network, channelType):
        evm = self.node.evm
        msg = message.ChannelAssignMessage(self.number, channelType, network.number)
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not assign (%.2x).' % (str(self), response))
        self.type = channelType
        self.network = network
    
    def setID(self, devType, devNum, transType):
        msg = message.ChannelIDMessage(self.number, devNum, devType, transType)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not set ID (%.2x).' % (str(self), response))
        self.device = Device(devNum, devType, transType)
    
    @property
    def searchTimeout(self):
        return self._searchTimeout
    @searchTimeout.setter
    def searchTimeout(self, timeout):
        msg = message.ChannelSearchTimeoutMessage(self.number, timeout)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not set search timeout (%.2x).' %
                               (str(self), response) )
        self._searchTimeout = timeout
    
    @property
    def period(self):
        return self._period
    @period.setter
    def period(self, counts):
        msg = message.ChannelPeriodMessage(self.number, counts)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not set period (%.2x).' % (str(self), response))
        self._period = counts
    
    @property
    def frequency(self):
        return self._frequency
    @frequency.setter
    def frequency(self, frequency):
        msg = message.ChannelFrequencyMessage(self.number, frequency)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s, could not set frequency (%.2x).' %
                               (str(self), response))
        self._frequency = frequency
    
    def open(self):
        msg = message.ChannelOpenMessage(number=self.number)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not open (%.2x).' % (str(self), response))
        
        evm.registerCallback(self)
    
    def close(self):
        msg = message.ChannelCloseMessage(number=self.number)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not close (%.2x).' % (str(self), response))
        
        while True:
            msg = evm.waitForMessage(message.ChannelEventResponseMessage)
            if msg.channelNumber == self.number and \
               msg.messageCode == EVENT_CHANNEL_CLOSED:
                break
        
        evm.removeCallback(self)
    
    def unassign(self):
        msg = message.ChannelUnassignMessage(number=self.number)
        evm = self.node.evm
        evm.writeMessage(msg)
        response = evm.waitForAck(msg)
        if response != RESPONSE_NO_ERROR:
            raise ChannelError('%s: could not unassign (0x%.2x).' % (str(self), response))
        self.network = None
    
    def registerCallback(self, callback):
        with self.evmCallbackLock:
            self.callbacks.add(callback)
    
    def process(self, msg):
        with self.evmCallbackLock:
            if isinstance(msg, ChannelMessage) and msg.channelNumber == self.number:
                for callback in self.callbacks:
                    try:
                        callback.process(msg, self)
                    except Exception as err:  # pylint: disable=broad-except
                        print(err)
    
    def __str__(self):
        rawstr = '<channel %d'
        device = self.device
        if device is not None:
            rawstr += ' (0x%.2x)' % device
        return rawstr + '>'


class Node(object):
    def __init__(self, driver):
        self.evm = event.EventMachine(driver)
        self.networks = []
        self.channels = []
        self.options = [0x00, 0x00, 0x00]
    
    running = property(lambda self: self.evm.running)
    
    def reset(self, wait=True):
        evm = self.evm
        evm.writeMessage(message.SystemResetMessage())
        if wait:
            evm.waitForMessage(message.StartupMessage)
    
    def start(self):
        if self.running:
            raise NodeError('Could not start ANT node (already started).')
        
        evm = self.evm
        evm.start()
        
        try:
            self.reset()
            evm.writeMessage(message.ChannelRequestMessage(
                                                messageID=MESSAGE_CAPABILITIES))
            caps = evm.waitForMessage(message.CapabilitiesMessage)
        except MessageError as err:
            self.stop()
            raise NodeError(err)
        else:
            self.networks = [ None ] * caps.maxNetworks
            self.channels = [ Channel(self, i) for i in xrange(0, caps.maxChannels) ]
            self.options = (caps.stdOptions, caps.advOptions, caps.advOptions2)

    def stop(self):
        if not self.running:
            raise NodeError('Could not stop ANT node (not started).')
        
        self.reset(wait=False)
        self.evm.stop()
    
    def getCapabilities(self):
        return (len(self.channels), len(self.networks), self.options)
    
    def setNetworkKey(self, number, key=None):
        networks = self.networks
        if key is not None:
            networks[number] = key
        
        network = networks[number]
        msg = message.NetworkKeyMessage(number, network.key)
        evm = self.evm
        evm.writeMessage(msg)
        evm.waitForAck(msg)
        network.number = number
    
    def getFreeChannel(self):
        for channel in self.channels:
            if channel.network is None:
                return channel
        raise NodeError('Could not find free channel.')
    
    def registerEventListener(self, callback):
        self.evm.registerCallback(callback)
