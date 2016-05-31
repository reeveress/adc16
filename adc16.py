import time
import copy
import corr
import os
import sys
import numpy as np
import struct
import logging
from pprint import pprint
katcp_port=7147


# Provides KATCP wrapper around ADC16 based CASPER design.  Includes many
# convenience functions for writing to the registers of the ADC chips,
# calibrating the SERDES blocks, and accessing status info about the ADC16
# design and clock status.  While most access will be done via the methods of
# this class, there may be occasion to access the ADC16 controller directly
# (via the #adc16_controller method, which returns a KATCP::Bram object).
#
# Here is the memory map for the underlying #adc16_controller device:
#
#   # ======================================= #
#   # ADC16 3-Wire Register (word 0)          #
#   # ======================================= #
#   # LL = Clock locked bits                  #
#   # NNNN = Number of ADC chips supported    #
#   # RR = ROACH2 revision expected/required  #
#   # C = SCLK                                #
#   # D = SDATA                               #
#   # 7 = CSNH (chip select H, active high)   #
#   # 6 = CSNG (chip select G, active high)   #
#   # 5 = CSNF (chip select F, active high)   #
#   # 4 = CSNE (chip select E, active high)   #
#   # 3 = CSND (chip select D, active high)   #
#   # 2 = CSNC (chip select C, active high)   #
#   # 1 = CSNB (chip select B, active high)   #
#   # 0 = CSNA (chip select A, active high)   #
#   # ======================================= #
#   # |<-- MSb                       LSb -->| #
#   # 0000_0000_0011_1111_1111_2222_2222_2233 #
#   # 0123_4567_8901_2345_6789_0123_4567_8901 #
#   # ---- --LL ---- ---- ---- ---- ---- ---- #
#   # ---- ---- NNNN ---- ---- ---- ---- ---- #
#   # ---- ---- ---- --RR ---- ---- ---- ---- #
#   # ---- ---- ---- ---- ---- --C- ---- ---- #
#   # ---- ---- ---- ---- ---- ---D ---- ---- #
#   # ---- ---- ---- ---- ---- ---- 7654 3210 #
#   # |<--- Status ---->| |<--- 3-Wire ---->| #
#   # ======================================= #
#   # NOTE: LL reflects the runtime lock      #
#   #       status of a line clock from each  #
#   #       ADC board.  A '1' bit means       #
#   #       locked (good!).  Bit 5 is always  #
#   #       used, but bit 6 is only used when #
#   #       NNNN is 4 (or less).              #
#   # ======================================= #
#   # NOTE: NNNN and RR are read-only values  #
#   #       that are set at compile time.     #
#   #       They do not indicate the state    #
#   #       of the actual hardware in use     #
#   #       at runtime.                       #
#   # ======================================= #
#
#   # ======================================= #
#   # ADC16 Control Register (word 1)         #
#   # ======================================= #
#   # W  = Deux write-enable                  #
#   # MM = Demux mode                         #
#   # R = ADC16 Reset                         #
#   # S = Snap Request                        #
#   # H = ISERDES Bit Slip Chip H             #
#   # G = ISERDES Bit Slip Chip G             #
#   # F = ISERDES Bit Slip Chip F             #
#   # E = ISERDES Bit Slip Chip E             #
#   # D = ISERDES Bit Slip Chip D             #
#   # C = ISERDES Bit Slip Chip C             #
#   # B = ISERDES Bit Slip Chip B             #
#   # A = ISERDES Bit Slip Chip A             #
#   # T = Delay Tap
#   # i = Bitslip specific channel(out of 8)  #
#   # ======================================= #
#   # |<-- MSb                       LSb -->| #
#   # 0000 0000 0011 1111 1111 2222 2222 2233 #
#   # 0123 4567 8901 2345 6789 0123 4567 8901 #
#   # ---- -WMM ---- ---- ---- ---- ---- ---- #
#   # ---- ---- ---R ---- ---- ---- ---- ---- #
#   # ---- ---- ---- ---S ---- ---- ---- ---- #
#   # ---- ---- ---- ---- HGFE DCBA iii- ---- #
#   # ---- ---- ---- ---- ---- ---- ---T TTTT #
#   # ======================================= #
#   # NOTE: W enables writing the MM bits.    #
#   #       Some of the other bits in this    #
#   #       register are one-hot.  Using      #
#   #       W ensures that the MM bits will   #
#   #       only be written to when desired.  #
#   #       00: demux by 1 (single channel)   #
#   # ======================================= #
#   # NOTE: MM selects the demux mode.        #
#   #       00: demux by 1 (single channel)   #
#   #       01: demux by 2 (dual channel)     #
#   #       10: demux by 4 (quad channel)     #
#   #       11: undefined                     #
#   #       ADC board.  A '1' bit means       #
#   #       locked (good!).  Bit 5 is always  #
#   #       used, but bit 6 is only used when #
#   #       NNNN is 4 (or less).              #
#   # ======================================= #
#
#   # =============================================== #
#   # ADC16 Delay A Strobe Register (word 2)          #
#   # =============================================== #
#   # D = Delay Strobe (rising edge active)           #
#   # =============================================== #
#   # |<-- MSb                              LSb -->|  #
#   # 0000  0000  0011  1111  1111  2222  2222  2233  #
#   # 0123  4567  8901  2345  6789  0123  4567  8901  #
#   # DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  #
#   # |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  #
#   # H4 H1 G4 G1 F4 F1 E4 E1 D4 D1 C4 C1 B4 B1 A4 A1 #
#   # =============================================== #
#
#   # =============================================== #
#   # ADC0 Delay B Strobe Register (word 3)           #
#   # =============================================== #
#   # D = Delay Strobe (rising edge active)           #
#   # =============================================== #
#   # |<-- MSb                              LSb -->|  #
#   # 0000  0000  0011  1111  1111  2222  2222  2233  #
#   # 0123  4567  8901  2345  6789  0123  4567  8901  #
#   # DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  DDDD  #
#   # |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  #
#   # H4 H1 G4 G1 F4 F1 E4 E1 D4 D1 C4 C1 B4 B1 A4 A1 #
#   # =============================================== #



class ADC16():#katcp.RoachClient):

	def __init__(self,**kwargs):
		if kwargs['verbosity'] == True:
			logging.basicConfig(level = logging.DEBUG)
		else:
			logging.basicConfig(level = logging.INFO)
		#katcp_port to connect to with FpgaClient			
		self.katcp_port = 7147
		#Make a dictionary out of chips specified on command line. 
		#mapping chip letters to numbers to facilitate writing to adc16_controller
		self.test_pattern = kwargs['test_pattern']	
		self.demux_mode = kwargs['demux_mode']		

		#create a chip dictionary to facilitate writing to adc16_controller	
		self.chips = {}
		self.chip_select_a = 0
		self.chip_select_b = 0
		self.chip_select_c = 0
		for chip in kwargs['chips']:
			if chip == 'a'or chip == 'A':
				self.chips['a'] = 0
				self.chip_select_a = 1 << self.chips['a']
			elif chip == 'b' or chip ==  'B':
				self.chips['b'] = 1
				self.chip_select_b = 1 << self.chips['b']
			elif chip == 'c' or chip == 'C':
				self.chips['c'] = 2
				self.chip_select_c = 1 << self.chips['c']
			else:
				logging.error('Invalid chip name passed, available values: a, b or c, default is all chips selected')
				exit(1)
		self.chip_select = self.chip_select_a | self.chip_select_b | self.chip_select_c
		print('Chips select:',bin(self.chip_select))
		#Creating an expected pattern dictionary. Used in test_taps and enable_pattern
		if self.test_pattern == 'deskew':
			self.expected = 0x2a;
		elif self.test_pattern  == 'sync':
			self.expected = 0x70;
		elif self.test_pattern == 'ramp':
			self.expected = 'ramp'
		else:
			if len(self.test_pattern) <= 8:
				self.expected = int(self.test_pattern,2)
			else:
				print('Not an acceptable test pattern. Check out -h at the terminal')
				exit(1) 
		#Instantiating a snap object with attributes of FpgaClient class
		print('Connecting to SNAP.....')
		self.snap = corr.katcp_wrapper.FpgaClient(kwargs['host'], self.katcp_port, timeout=10)
		time.sleep(1)

		if  self.snap.is_connected():
			print('Connected to SNAP!')	
		else:
			logging.error('Couldn\'t connect to SNAP, check your connection..')
			exit(1)
		#Dealing with flags passed into argsparse at the prompt by the user
		if kwargs['skip_flag'] == True:
			print('Not programming the bof file')
		else:
			print('Programming the bof file....')
			self.snap.progdev(kwargs['bof'])
			print('Programmed!')

		
	#write_adc is used for writing specific ADC registers.
	#ADC controller can only write to adc one bit at a time at rising clock edge
	def write_adc(self,addr,data):
		SCLK = 0x200
		CS = self.chip_select
		IDLE = SCLK
		SDA_SHIFT = 8
		self.snap.write_int('adc16_controller',IDLE,offset=0,blindwrite=True)
		for i in range(8):
			addr_bit = (addr>>(8-i-1))&1
			state = (addr_bit<<SDA_SHIFT) | CS
			self.snap.write_int('adc16_controller',state,offset=0,blindwrite=True)
			logging.debug("Printing address state written to adc16_controller, offset=0, clock low")
			logging.debug(np.binary_repr(state,width=32))
	#		print(np.binary_repr(state,width=32))
			state = (addr_bit<<SDA_SHIFT) | CS | SCLK
			self.snap.write_int('adc16_controller',state,offset=0,blindwrite=True)
			logging.debug("Printing address state written to adc16_controller, offset=0, clock high")
			logging.debug(np.binary_repr(state,width=32))
	#		print(np.binary_repr(state,width=32))
		for j in range(16):
			data_bit = (data>>(16-j-1))&1
			state = (data_bit<<SDA_SHIFT) | CS
			self.snap.write_int('adc16_controller',state,offset=0,blindwrite=True)
			logging.debug("Printing data state written to adc16_controller, offset=0, clock low")
			logging.debug(np.binary_repr(state,width=32))
	#		print(np.binary_repr(state,width=32))
			state =( data_bit<<SDA_SHIFT) | CS | SCLK	
			self.snap.write_int('adc16_controller',state,offset=0,blindwrite=True)		
			logging.debug("Printing data address state written to adc16_controller, offset=0, clock high")
			logging.debug(np.binary_repr(state,width=32))
	#		print(np.binary_repr(state,width=32))
		
		self.snap.write_int('adc16_controller',IDLE,offset=0,blindwrite=True)

	def power_cycle(self):
		logging.info('Power cycling the ADC')
		#power adc down
		self.write_adc(0x0f,0x0200)	
                #power adc up
		self.write_adc(0x0f,0x0000)

	def adc_reset(self):
		logging.info('Initializing le ADC')
		#reset adc	
                self.write_adc(0x00,0x0001)

	def adc_initialize(self):
		self.adc_reset()
		#power adc down
		self.write_adc(0x0f,0x0200)
		#select operating mode
		self.set_demux_adc()
		#power adc up
		self.write_adc(0x0f,0x0000)
		
#	def supports_demux(self):
#                #adc16_controller supports demux modes if the W bit(0x04000000) is not set to 1 so this function returns true if
#                #adc16_controller supports demux modes (set by the firmware)
#                self.snap.write_int('adc16_controller',0x04000000,offset=1)
#                #first write the bit, then read it, if it's NOT there anymore then setting demux mode is supported  
#                #Setting W bit to 1:
#                self.snap.write_int('adc16_controller', 0x0400_0000, offset=1)
#                #reading adc16_controller and returing True if it is 0, which means that W bit could not be written to
#                return (self.snap.read_int('adc16_controller',offset=1)==0)





	def set_demux_adc(self):
		if self.demux_mode==1:
			self.write_adc(0x31,0x04) 
			#clock dividing register set to 1 (demux 1 means four signals going in)
			self.write_adc(0x31,0x000)
			
		elif self.demux_mode==2:
			self.write_adc(0x31,0x02) 
			#clock dividing register set to 2
			self.write_adc(0x31,0x100)
			print('Route signals into input 1 and input 3 of the selected ADC chip')
			#Selecting input 1
			#Selecting input 3
		elif self.demux_mode==4:
			self.write_adc(0x31,0x01)
			#clock dividing register set to 4 (1GHz clock get's divided by 4 and fed to the 4 channels at 250MHz)
			self.write_adc(0x31,0x200)
			print('Route signal into input 1 of the selected ADC chip')
			#Selecting input 1
		else:
		 	logging.error('demux_mode variable not assigned. Weird.')
			exit(1)
	#There are two different 
	def set_demux_fpga(self):
		demux_shift = 24
                if self.demux_mode==1:
			state = (4+0) << demux_shift
			self.snap.write_int('adc16_controller', state, offset = 1, blindwrite = True)
                elif self.demux_mode==2:
			#writing the WW enable bit(4) as well as the demux setting bit(1 for demux mode 2 as seen in the adc16_controller memory map)
			state = (4+1) << demux_shift
			self.snap.write_int('adc16_controller', state, offset = 1, blindwrite = True)
                elif self.demux_mode==4:
			state = (4+2) << demux_shift
			self.snap.write_int('adc16_controller', state, offset = 1, blindwrite = True)
                else:
                        print('Invalid or no demux mode specified')
                        exit(1)


	def adc16_based(self):
                if 'adc16_controller' in self.snap.listdev():
                        print('Design is ADC16-based')
                else:
			print('Design is not ADC16-based')
			exit(1)





# Selects a test pattern or sampled data for all ADCs selected by
  # +chip_select+.  +ptn+ can be any of:
  #
  #   :ramp            Ramp pattern 0-255
  #   :deskew (:eye)   Deskew pattern (10101010)
  #   :sync (:frame)   Sync pattern (11110000)
  #   :custom1         Custom1 pattern
  #   :custom2         Custom2 pattern
  #   :dual            Dual custom pattern
  #   :none            No pattern (sampled data)
  #
  # Default is :ramp.  Any value other than shown above is the same as :none
  # (i.e. pass through sampled data).

	def enable_pattern(self,pattern):                                                                                           
		self.write_adc(0x25,0x00)
		self.write_adc(0x45,0x00)
                if pattern =='ramp':
                        self.write_adc(0x25,0x0040)
                elif pattern == 0x2a:
                        self.write_adc(0x45,0x0001)
                elif pattern == 0x70:
                        self.write_adc(0x45,0x0002)
		else:
			self.write_adc(0x25,0x10)
			self.write_adc(0x26,(self.expected)<<8)
		time.sleep(1)

	def read_ram(self,device):
		SNAP_REQ = 0x00010000
		self.snap.write_int('adc16_controller',0, offset=1,blindwrite=True)
		self.snap.write_int('adc16_controller',SNAP_REQ, offset=1,blindwrite=True)
		#Read the device that is passed to the read_ram method,1024 elements at a time,snapshot is a binary string that needs to get unpacked
		#Part of the read request is the size parameter,1024, which specifies the amount of bytes to read form the device
		snapshot = self.snap.read(device,1024,offset=0)
		
		#struct unpack returns a tuple of signed int values. 
		#Since we're requesting to read adc16_wb_ram at a size of 1024 bytes, we are unpacking 
		#1024 bytes each of which is a signed char(in C, python only knows ints). Unpacking as
		#a signed char is for mapping purposes:

		# ADC returns values from 0 to 255 (since it's an 8 bit ADC), the voltage going into ADC
		# varies from -1V to 1V, we want 0 to mean 0, not -1 volts so we need to remap the output 
		# of the ADC to something more sensible, like -128 to 127. That way 0 volts corresponds to 
		# a 0 value in the unpacked data. 
		string_data = struct.unpack('>1024b', snapshot)
		#Converting the tuple into a vector of 1024 elements
		array_data = np.array(string_data)
#		for i in range(array_data.shape[0]):
#			print('{:08b}'.format(array_data[i]))	
		#print(array_data)
		return array_data
#			
#			
#			x = bin(array_data[i])
#			print('{:>010b}'.format(x))
#		print(array_data.shape)
#		j = 0
#		k = 1
#		while j < 1024:
#			print('{:08b}'.format(array_data[j]))	
#			print('{:08b}'.format(array_data[k]))	
#			j += 8
#			k += 8
	#function that tests taps, it shifts data checks with the expected data and ouputs the error count

	
	#The ADC16 controller word (the offset in write_int method) 2 and 3 are for delaying taps of A and B lanes, respectively.
	#Refer to the memory map word 2 and word 3 for clarification. The memory map was made for a ROACH design so it has chips A-H. 
	#SNAP 1 design has three chips
	def bitslip(self,chip_num,channel):
		chan_shift = 5
		chan_select_bs = channel << chan_shift
		state = 0
		chip_shift = 8
		#chip_select_bs = 1 << chip_shift + chip_num
		chip_select_bs = (2**8-1)<<8
		state |= (chip_select_bs | chan_select_bs)
	#	print('Bitslip state written to offset=1:',bin(state))
		self.snap.write_int('adc16_controller', 0, offset=1, blindwrite=True)
		self.snap.write_int('adc16_controller', state, offset=1, blindwrite=True)
		self.snap.write_int('adc16_controller', 0, offset=1, blindwrite=True)
			
		
	def delay_tap(self,tap,channel):
		

		if channel == 'all':
			chan_select = 0xff
			

			delay_tap_mask = 0x1f
			self.snap.write_int('adc16_controller', 0 , offset = 2,blindwrite=True)
			self.snap.write_int('adc16_controller', 0 , offset = 3,blindwrite=True)
			#Set tap bits
			self.snap.write_int('adc16_controller', delay_tap_mask & tap , offset = 1,blindwrite=True)
			#Set strobe bits
			self.snap.write_int('adc16_controller', 0xffffffff, offset = 2,blindwrite=True)
			self.snap.write_int('adc16_controller', 0xffffffff, offset = 3,blindwrite=True)
			#Clear all bits
			self.snap.write_int('adc16_controller', 0 , offset = 1,blindwrite=True)
			self.snap.write_int('adc16_controller', 0 , offset = 2,blindwrite=True)
			self.snap.write_int('adc16_controller', 0 , offset = 3,blindwrite=True)
			#Note this return statement, after all channels have been bitslip it'll exit out of the function. 
			#the function is called again after figuring out the best tap with a single channel argument. 
			return
		elif channel == '1a':
			chan_select = 0x111
			lane_offset = 2
		elif channel == '1b':
			chan_select = 0x111
			lane_offset = 3
		elif channel == '2a':
			chan_select = 0x222
			lane_offset = 2
		elif channel == '2b':
			chan_select = 0x222
			lane_offset = 3
		elif channel == '3a':
			chan_select = 0x444
			lane_offset = 2
		elif channel == '3b':
			chan_select = 0x444
			lane_offset = 3
		elif channel == '4a':
			chan_select = 0x888
			lane_offset = 2
		elif channel == '4b':
			chan_select = 0x888
			lane_offset = 3
		



		delay_tap_mask = 0x1f
		self.snap.write_int('adc16_controller', 0 , offset = lane_offset,blindwrite=True)
		#Set tap bits
		self.snap.write_int('adc16_controller', delay_tap_mask & tap , offset = 1,blindwrite=True)
		#Set strobe bits
		self.snap.write_int('adc16_controller', chan_select , offset = lane_offset,blindwrite=True)
		#Clear all bits
		self.snap.write_int('adc16_controller', 0 , offset = 1,blindwrite=True)
		self.snap.write_int('adc16_controller', 0 , offset = 2,blindwrite=True)
		self.snap.write_int('adc16_controller', 0 , offset = 3,blindwrite=True)
	

	#returns an array of error counts for a given tap(assume structure chan 1a, chan 1b, chan 2a, chan 2b etc.. until chan 4b
	#taps argument can have a value of an int or a string. If it's a string then it will iterate through all 32 taps 
	#if it's an int it will only delay all channels by that particular tap value.
	def test_tap(self,chip,taps):
		if taps  == 'all':			
			
			error_count=[]
			#read_ram reuturns an array of data form a sanpshot from ADC output
			for tap in range(32):	
				
				self.delay_tap(tap,'all')
				data = self.read_ram('adc16_wb_ram{0}'.format(self.chips[chip]))
				#each tap will return an error count for each channel and lane, so an array of 8 elements with an error count for each

				chan1a_error = 0
				chan1b_error = 0
				chan2a_error = 0
				chan2b_error = 0
				chan3a_error = 0
				chan3b_error = 0
				chan4a_error = 0
				chan4b_error = 0
			
			
			
			
				i=0
				while i < 1024:
					if data[i] != self.expected:
						chan1a_error += 1
					if data[i+1] != self.expected:
						chan1b_error += 1
					if data[i+2] != self.expected:
						chan2a_error += 1
					if data[i+3] != self.expected:
						chan2b_error += 1
					if data[i+4] != self.expected:
						chan3a_error += 1

					if data[i+5] != self.expected:
						chan3b_error += 1
					if data[i+6] != self.expected:
						chan4a_error += 1
					if data[i+7] != self.expected:
						chan4b_error += 1
					i += 8

				error_count.append([chan1a_error,chan1b_error, chan2a_error, chan2b_error, chan3a_error, chan3b_error, chan4a_error, chan4b_error])
			return(error_count)
		else:

			error_count=[]
			#read_ram reuturns an array of data form a sanpshot from ADC output
				
			self.delay_tap(taps,'all')
			data = self.read_ram('adc16_wb_ram{0}'.format(self.chips[chip]))
			#each tap will return an error count for each channel and lane, so an array of 8 elements with an error count for each

			chan1a_error = 0
			chan1b_error = 0
			chan2a_error = 0
			chan2b_error = 0
			chan3a_error = 0
			chan3b_error = 0
			chan4a_error = 0
			chan4b_error = 0
		
		
		
		
			i=0
			while i < 1024:
				if data[i] != self.expected:
					chan1a_error += 1
				if data[i+1] != self.expected:
					chan1b_error += 1
				if data[i+2] != self.expected:
					chan2a_error += 1
				if data[i+3] != self.expected:
					chan2b_error += 1
				if data[i+4] != self.expected:
					chan3a_error += 1

				if data[i+5] != self.expected:
					chan3b_error += 1
				if data[i+6] != self.expected:
					chan4a_error += 1
				if data[i+7] != self.expected:
					chan4b_error += 1
				i += 8

			error_count.append([chan1a_error,chan1b_error, chan2a_error, chan2b_error, chan3a_error, chan3b_error, chan4a_error, chan4b_error])
			print('Error count for {0} tap: {1}'.format(taps,error_count))
			return(error_count)
	def walk_taps(self):
		for chip,chip_num in self.chips.iteritems():
			logging.info('Callibrating chip %s...'%chip)	
			logging.info('Setting deskew pattern...')
			print('Stuff in chip %s before enabling pattern'%chip)
			print(self.read_ram('adc16_wb_ram{0}'.format(chip_num)))
			
			self.enable_pattern(self.expected)
			print('Stuff in chip after enabling test mode\n')
			print(self.read_ram('adc16_wb_ram{0}'.format(chip_num)))
			#check if either of the extreme tap setting returns zero errors in any one of the channels. Bitslip if True. 
			#This is to make sure that the eye of the pattern is swept completely
#			error_counts_0 = self.test_tap(chip,0)
#			error_counts_31 = self.test_tap(chip,31)
#
#			for i in range(8):
#				print('Bitslipping chan %i' %i)
#				while not(error_counts_0[0][i]):^ and not(error_counts_31[0][i]): 
#					self.bitslip(chip_num,i)
#					error_counts_0 = self.test_tap(chip,0)
#					error_counts_31 = self.test_tap(chip,31)
						
#			for i in range(8):
#				
#				error_counts = self.test_tap(chip,'all')
#				print('Before bitslip of chan {0}'.format(i))
#				pprint(error_counts)
#				for j in range(8):
#
#					self.bitslip(chip_num,i)
#				error_counts = self.test_tap(chip,'all')
#				print('After bitslip of chan {0}'.format(i))
#				pprint(error_counts)


			#error_list is a list of 32 'rows'(corresponding to the 32 taps) , each row containing 8 elements,each element is the number of errors  	
			#of that lane  when compared to the expected value. read_ram method unpacks 1024 bytes. There are 8
			#lanes so each lane gets 1024/8=128 read outs from a single call to read_ram method, like this, channel_1a etc. represent the errors in that channel
			# tap 0: [ channel_1a channel_1b channel_2a channel_2b channel_3a channel_3b channel_4a channel_4b]
			# tap 1: [ channel_1a channel_1b channel_2a channel_2b channel_3a channel_3b channel_4a channel_4b]
			# .....: [ channel_1a channel_1b channel_2a channel_2b channel_3a channel_3b channel_4a channel_4b]
			# tap 31:[ channel_1a channel_1b channel_2a channel_2b channel_3a channel_3b channel_4a channel_4b]
			error_list = self.test_tap(chip,'all')
			good_tap_range = []	
			best_tap_range = []
			print('Printing the list of errors, each row is a tap\n')
			pprint(['chan1a','chan1b','chan2a','chan2b','chan3a','chan3b','chan4a','chan4b'])
			pprint(error_list)
			min_tap=[]
			max_tap=[]
			#This loop goes through error_list, finds the elements with a value of 0 and appends them to the good tap range list 
			#It also picks out the elements corresponding to different channels and groups them together. The error_list is a list where each 'row' is a different tap
			#I wanted to find the elements in each channel that have zero errors, group the individual channels, and get the value of the tap in which they're in - which is the index of the row
			for i in range(8):
				good_tap_range.append([])
				#j represents the tap value
				for j in range(32):
					#i represents the channel/lane value
					if error_list[j][i]==0:
						good_tap_range[i].append(j)
		#	find the min and max of each element of good tap range and call delay tap 
			logging.info('Printing good tap values for each channel...each row corresponds to different channel')
				
			for i in range(len(good_tap_range)):
				print('Channel {0}: {1}'.format(i+1,good_tap_range[i]))

			channels = ['1a','1b','2a','2b','3a','3b','4a','4b']
			for k in range(8):
				min_tap = min(good_tap_range[k])
				max_tap = max(good_tap_range[k])

				best_tap = (min_tap+max_tap)/2
			#	print(best_tap)
				self.delay_tap(best_tap,channels[k])
			print('Printing the calibrated data from ram{0}.....'.format(self.chips[chip]))
			pprint(self.read_ram('adc16_wb_ram{0}'.format(self.chips[chip])))




#			BITSLIP
#			self.enable_pattern('sync')
#			snap = self.read_ram('adc16_wb_ram{0}'.format(chip_num))
#			print('Snapshot before bitslipping:\n')
#			pprint(snap)
#			for i in range(8):
#				self.bitslip(chip_num,i)
#				snap = self.read_ram('adc16_wb_ram{0}'.format(chip_num))
#				print('Snapshot after bitslipping:\n')
#				pprint(snap)
			
	def sync_chips(self):
			
		#channels = {0:'1a',1:'1b',2:'2a',3:'2b',4:'3a',5:'3b',6:'4a',7:'4b'}
		self.enable_pattern('sync')
		self.sync_expected = 0x70
		 			
		for key,value in self.chips.iteritems():
			#self.chip_select(key)
			snapshot = self.read_ram('adc16_wb_ram%i' %value)
			logging.info('Printing the sync pattern snapshot\n')
			pprint(snapshot)
			for lane in range(8):
				if snapshot[lane] != self.sync_expected:
					self.bitslip(value,lane)
			
	def clock_locked(self):
		locked_bit = self.snap.read_int('adc16_controller',offset=0) >> 24
		if locked_bit & 3:
			logging.info('ADC clock is locked!!!')
			print(self.snap.est_brd_clk())
		else:
			logging.warning('ADC clock not locked, check your clock source/correctly set demux mode')
	def calibrate(self):
		self.adc_initialize()
		#check if clock is locked
		self.clock_locked()
		#check if design is ADC16 based
		self.adc16_based()
		#Calibrate ADC by going through various tap values
		self.walk_taps()
		self.sync_chips()
			








