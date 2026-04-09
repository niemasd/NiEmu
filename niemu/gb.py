#! /usr/bin/env python3
'''
Nintendo Game Boy Emulator

https://gbdev.io/pandocs
https://meganesu.github.io/generate-gb-opcodes
https://rgbds.gbdev.io/docs/v1.0.1/gbz80.7
'''

# imports
from niemu.common import COLOR_BLACK, COLOR_GRAY_DARK, COLOR_GRAY_LIGHT, COLOR_WHITE, load_game_data, Memory, Register8, Register8Pair, Register16
from numpy import int8, uint16
import pygame

# constants
WIDTH = 160
HEIGHT = 144
FPS = 59.73
COLOR_PALETTE = [COLOR_WHITE, COLOR_GRAY_LIGHT, COLOR_GRAY_DARK, COLOR_BLACK]

# class to represent Game Boy PPU
class PPU:
    # initialize a PPU object
    def __init__(self, memory):
        self.memory = memory
        self.memory[0xFF40] = 0x91  # LCDC: LCD on, BG on, tile data 0x8000, BG map 0x9800
        self.memory[0xFF42] = 0x00  # SCY
        self.memory[0xFF43] = 0x00  # SCX
        self.memory[0xFF44] = 0x00  # LY
        self.memory[0xFF47] = 0xE4  # BGP

    # register helpers
    def lcd_enabled(self):
        return bool(self.memory[0xFF40] & 0x80)
    def bg_enabled(self):
        return bool(self.memory[0xFF40] & 0x01)

    # convert til color ID (0, 1, 2, or 3) into RGB tuple
    def get_bg_palette_color(self, color_id):
        bgp = int(self.memory[0xFF47])
        shade = (bgp >> (color_id * 2)) & 0b11
        return COLOR_PALETTE[shade]

    # read one pixel from a tile
    def get_tile_pixel(self, tile_addr, x, y):
        bit = 7 - x
        row_addr = tile_addr + (y * 2)
        low = int(self.memory[row_addr])
        high = int(self.memory[row_addr + 1])
        lo_bit = (low >> bit) & 1
        hi_bit = (high >> bit) & 1
        return (hi_bit << 1) | lo_bit

    # resolve background tile number to tile data address
    def get_bg_tile_addr(self, tile_number):
        lcdc = int(self.memory[0xFF40])
        if lcdc & 0x10:
            return 0x8000 + (tile_number * 16)
        else:
            signed_tile_number = int(int8(tile_number))
            return 0x9000 + (signed_tile_number * 16)

    # render scrolling background layer
    def render_background(self, surface):
        # set things up
        if not self.lcd_enabled():
            surface.fill(COLOR_WHITE)
            return
        if not self.bg_enabled():
            surface.fill(COLOR_WHITE)
            return
        lcdc = int(self.memory[0xFF40])
        scy = int(self.memory[0xFF42])
        scx = int(self.memory[0xFF43])

        # LCDC bit 3 selects background tile map
        bg_map_base = 0x9C00 if (lcdc & 0x08) else 0x9800
        for screen_y in range(HEIGHT):
            world_y = (screen_y + scy) & 0xFF
            tile_y = world_y // 8
            pixel_y = world_y % 8
            for screen_x in range(WIDTH):
                world_x = (screen_x + scx) & 0xFF
                tile_x = world_x // 8
                pixel_x = world_x % 8
                tile_map_index = tile_y * 32 + tile_x
                tile_number = int(self.memory[bg_map_base + tile_map_index])
                tile_addr = self.get_bg_tile_addr(tile_number)
                color_id = self.get_tile_pixel(tile_addr, pixel_x, pixel_y)
                surface.set_at((screen_x, screen_y), self.get_bg_palette_color(color_id))

    # render frame (just background for now)
    def render_frame(self, surface):
        self.render_background(surface)

    # placeholder for future timing-based PPU (right now just keep LY sane)
    def step(self, m_cycles):
        self.memory[0xFF44] = 0

# class to represent F register
class RegisterF(Register8):
    def set(self, value):
        super().set(value & 0xF0) # keep lower 4 bits cleared

# class to emulate Nintendo Game Boy
class GameBoy:
    # initialize a GameBoy object
    def __init__(self):
        # CPU flags
        self.interrupt_master_enable = False
        self.is_halted = False
        self.is_stopped = True

        # 8-bit registers
        self.A = Register8(0x01)
        self.F = RegisterF(0xB0)
        self.B = Register8(0x00)
        self.C = Register8(0x13)
        self.D = Register8(0x00)
        self.E = Register8(0xD8)
        self.H = Register8(0x01)
        self.L = Register8(0x4D)

        # 16-bit registers
        self.PC = Register16(0x0100)
        self.SP = Register16(0xFFFE)
        self.AF = Register8Pair(self.A, self.F)
        self.BC = Register8Pair(self.B, self.C)
        self.DE = Register8Pair(self.D, self.E)
        self.HL = Register8Pair(self.H, self.L)

        # memory, PPU, and other key variables
        self.memory = Memory(0x10000)
        self.ppu = PPU(self.memory)
        self.cartridge = None
        self.instructions = [None]*0x100
        self.instructions[0xCB] = [None]*0x100

        # define special instructions
        self.instructions[0x00] = self.NOP                    # 0x00 = NOP
        self.instructions[0x10] = self.STOP                   # 0x10 = STOP
        self.instructions[0x37] = self.SCF                    # 0x37 = SCF
        self.instructions[0x3F] = self.CCF                    # 0x3F = CCF
        self.instructions[0x76] = self.HALT                   # 0x76 = HALT
        self.instructions[0xF3] = lambda: self.set_IME(False) # 0xF3 = DI
        self.instructions[0xFB] = lambda: self.set_IME(True)  # 0xFB = EI

        # define LD ??, d16 instructions
        self.instructions[0x01] = lambda: self.LD_XX_d16(self.BC) # 0x01 = LD BC, d16
        self.instructions[0x11] = lambda: self.LD_XX_d16(self.DE) # 0x11 = LD DE, d16
        self.instructions[0x21] = lambda: self.LD_XX_d16(self.HL) # 0x21 = LD HL, d16
        self.instructions[0x31] = lambda: self.LD_XX_d16(self.SP) # 0x31 = LD SP, d16

        # define LD (??), ? instructions
        self.instructions[0x02] = lambda: self.LD_addr_X(self.A, self.BC,  0) # 0x02 = LD (BC), A
        self.instructions[0x12] = lambda: self.LD_addr_X(self.A, self.DE,  0) # 0x12 = LD (DE), A
        self.instructions[0x22] = lambda: self.LD_addr_X(self.A, self.HL,  1) # 0x22 = LD (HL+), A
        self.instructions[0x32] = lambda: self.LD_addr_X(self.A, self.HL, -1) # 0x32 = LD (HL-), A
        self.instructions[0x70] = lambda: self.LD_addr_X(self.B, self.HL,  0) # 0x70 = LD (HL), B
        self.instructions[0x71] = lambda: self.LD_addr_X(self.C, self.HL,  0) # 0x71 = LD (HL), C
        self.instructions[0x72] = lambda: self.LD_addr_X(self.D, self.HL,  0) # 0x72 = LD (HL), D
        self.instructions[0x73] = lambda: self.LD_addr_X(self.E, self.HL,  0) # 0x73 = LD (HL), E
        self.instructions[0x74] = lambda: self.LD_addr_X(self.H, self.HL,  0) # 0x74 = LD (HL), H
        self.instructions[0x75] = lambda: self.LD_addr_X(self.L, self.HL,  0) # 0x75 = LD (HL), L
        self.instructions[0x77] = lambda: self.LD_addr_X(self.A, self.HL,  0) # 0x77 = LD (HL), A

        # define INC ?? instructions
        self.instructions[0x03] = lambda: self.INC_XX(self.BC) # 0x03 = INC BC
        self.instructions[0x13] = lambda: self.INC_XX(self.DE) # 0x13 = INC DE
        self.instructions[0x23] = lambda: self.INC_XX(self.HL) # 0x23 = INC HL
        self.instructions[0x33] = lambda: self.INC_XX(self.SP) # 0x33 = INC SP

        # define INC ? instructions
        self.instructions[0x04] = lambda: self.INC_X(self.B)     # 0x04 = INC B
        self.instructions[0x0C] = lambda: self.INC_X(self.C)     # 0x0C = INC C
        self.instructions[0x14] = lambda: self.INC_X(self.D)     # 0x14 = INC D
        self.instructions[0x1C] = lambda: self.INC_X(self.E)     # 0x1C = INC E
        self.instructions[0x24] = lambda: self.INC_X(self.H)     # 0x24 = INC H
        self.instructions[0x2C] = lambda: self.INC_X(self.L)     # 0x2C = INC L
        self.instructions[0x3C] = lambda: self.INC_X(self.A)     # 0x3C = INC A
        self.instructions[0x34] = lambda: self.INC_addr(self.HL) # 0x34 = INC (HL)

        # define DEC ? instructions
        self.instructions[0x05] = lambda: self.DEC_X(self.B)     # 0x05 = DEC B
        self.instructions[0x0D] = lambda: self.DEC_X(self.C)     # 0x0D = DEC C
        self.instructions[0x15] = lambda: self.DEC_X(self.D)     # 0x15 = DEC D
        self.instructions[0x1D] = lambda: self.DEC_X(self.E)     # 0x1D = DEC E
        self.instructions[0x25] = lambda: self.DEC_X(self.H)     # 0x25 = DEC H
        self.instructions[0x2D] = lambda: self.DEC_X(self.L)     # 0x2D = DEC L
        self.instructions[0x3D] = lambda: self.DEC_X(self.A)     # 0x3D = DEC A
        self.instructions[0x35] = lambda: self.DEC_addr(self.HL) # 0x35 = DEC (HL)

        # define LD ?, d8 instructions
        self.instructions[0x06] = lambda: self.LD_X_d8(self.B)     # 0x06 = LD B, d8
        self.instructions[0x0E] = lambda: self.LD_X_d8(self.C)     # 0x0E = LD C, d8
        self.instructions[0x16] = lambda: self.LD_X_d8(self.D)     # 0x16 = LD D, d8
        self.instructions[0x1E] = lambda: self.LD_X_d8(self.E)     # 0x1E = LD E, d8
        self.instructions[0x26] = lambda: self.LD_X_d8(self.H)     # 0x26 = LD H, d8
        self.instructions[0x2E] = lambda: self.LD_X_d8(self.L)     # 0x2E = LD L, d8
        self.instructions[0x3E] = lambda: self.LD_X_d8(self.A)     # 0x3E = LD A, d8
        self.instructions[0x36] = lambda: self.LD_addr_d8(self.HL) # 0x36 = LD (HL), d8

        # define JR instructions
        self.instructions[0x18] = lambda: self.JR_s8(True)                  # 0x18 = JR s8
        self.instructions[0x20] = lambda: self.JR_s8(not self.get_flag_Z()) # 0x20 = JR NZ, s8
        self.instructions[0x28] = lambda: self.JR_s8(self.get_flag_Z())     # 0x28 = JR Z, s8
        self.instructions[0x30] = lambda: self.JR_s8(not self.get_flag_C()) # 0x30 = JR NC, s8
        self.instructions[0x38] = lambda: self.JR_s8(self.get_flag_C())     # 0x38 = JR C, s8

        # define ADD ??, ?? instructions
        self.instructions[0x09] = lambda: self.ADD_XX_XX(self.HL, self.BC) # 0x09 = ADD HL, BC
        self.instructions[0x19] = lambda: self.ADD_XX_XX(self.HL, self.DE) # 0x19 = ADD HL, DE
        self.instructions[0x29] = lambda: self.ADD_XX_XX(self.HL, self.HL) # 0x29 = ADD HL, HL
        self.instructions[0x39] = lambda: self.ADD_XX_XX(self.HL, self.SP) # 0x39 = ADD HL, SP

        # define ADD ?, ? instructions
        self.instructions[0x80] = lambda: self.ADD_X_X(self.A, self.B) # 0x80 = ADD A, B
        self.instructions[0x81] = lambda: self.ADD_X_X(self.A, self.C) # 0x81 = ADD A, C
        self.instructions[0x82] = lambda: self.ADD_X_X(self.A, self.D) # 0x82 = ADD A, D
        self.instructions[0x83] = lambda: self.ADD_X_X(self.A, self.E) # 0x83 = ADD A, E
        self.instructions[0x84] = lambda: self.ADD_X_X(self.A, self.H) # 0x84 = ADD A, H
        self.instructions[0x85] = lambda: self.ADD_X_X(self.A, self.L) # 0x85 = ADD A, L
        self.instructions[0x87] = lambda: self.ADD_X_X(self.A, self.A) # 0x87 = ADD A, A

        # define ADC ?, ? instructions
        self.instructions[0x88] = lambda: self.ADD_X_X(self.A, self.B, carry=True) # 0x88 = ADC A, B
        self.instructions[0x89] = lambda: self.ADD_X_X(self.A, self.C, carry=True) # 0x89 = ADC A, C
        self.instructions[0x8A] = lambda: self.ADD_X_X(self.A, self.D, carry=True) # 0x8A = ADC A, D
        self.instructions[0x8B] = lambda: self.ADD_X_X(self.A, self.E, carry=True) # 0x8B = ADC A, E
        self.instructions[0x8C] = lambda: self.ADD_X_X(self.A, self.H, carry=True) # 0x8C = ADC A, H
        self.instructions[0x8D] = lambda: self.ADD_X_X(self.A, self.L, carry=True) # 0x8D = ADC A, L
        self.instructions[0x8F] = lambda: self.ADD_X_X(self.A, self.A, carry=True) # 0x8F = ADC A, A

        # define SUB ?, ? instructions
        self.instructions[0x90] = lambda: self.SUB_X_X(self.A, self.B) # 0x90 = SUB A, B
        self.instructions[0x91] = lambda: self.SUB_X_X(self.A, self.C) # 0x91 = SUB A, C
        self.instructions[0x92] = lambda: self.SUB_X_X(self.A, self.D) # 0x92 = SUB A, D
        self.instructions[0x93] = lambda: self.SUB_X_X(self.A, self.E) # 0x93 = SUB A, E
        self.instructions[0x94] = lambda: self.SUB_X_X(self.A, self.H) # 0x94 = SUB A, H
        self.instructions[0x95] = lambda: self.SUB_X_X(self.A, self.L) # 0x95 = SUB A, L
        self.instructions[0x97] = lambda: self.SUB_X_X(self.A, self.A) # 0x97 = SUB A, A

        # define SBC ?, ? instructions
        self.instructions[0x98] = lambda: self.SBC_X_X(self.A, self.B, carry=True) # 0x98 = SBC A, B
        self.instructions[0x99] = lambda: self.SBC_X_X(self.A, self.C, carry=True) # 0x99 = SBC A, C
        self.instructions[0x9A] = lambda: self.SBC_X_X(self.A, self.D, carry=True) # 0x9A = SBC A, D
        self.instructions[0x9B] = lambda: self.SBC_X_X(self.A, self.E, carry=True) # 0x9B = SBC A, E
        self.instructions[0x9C] = lambda: self.SBC_X_X(self.A, self.H, carry=True) # 0x9C = SBC A, H
        self.instructions[0x9D] = lambda: self.SBC_X_X(self.A, self.L, carry=True) # 0x9D = SBC A, L
        self.instructions[0x9F] = lambda: self.SBC_X_X(self.A, self.A, carry=True) # 0x9F = SBC A, A

        # define XOR ? instructions
        self.instructions[0xA8] = lambda: self.XOR(self.A, self.B)       # 0xA8 = XOR B
        self.instructions[0xA9] = lambda: self.XOR(self.A, self.C)       # 0xA9 = XOR C
        self.instructions[0xAA] = lambda: self.XOR(self.A, self.D)       # 0xAA = XOR D
        self.instructions[0xAB] = lambda: self.XOR(self.A, self.E)       # 0xAB = XOR E
        self.instructions[0xAC] = lambda: self.XOR(self.A, self.H)       # 0xAC = XOR H
        self.instructions[0xAD] = lambda: self.XOR(self.A, self.L)       # 0xAD = XOR L
        self.instructions[0xAF] = lambda: self.XOR(self.A, self.A)       # 0xAF = XOR A
        self.instructions[0xAE] = lambda: self.XOR_addr(self.A, self.HL) # 0xAE = XOR (HL)

        # define JP instructions
        self.instructions[0xC2] = lambda: self.JP_a16(not self.get_flag_Z()) # 0xC2 = JP NZ, a16
        self.instructions[0xC3] = lambda: self.JP_a16(True)                  # 0xC3 = JP a16
        self.instructions[0xCA] = lambda: self.JP_a16(self.get_flag_Z())     # 0xCA = JP Z, a16
        self.instructions[0xD2] = lambda: self.JP_a16(not self.get_flag_C()) # 0xD2 = JP NC, a16
        self.instructions[0xDA] = lambda: self.JP_a16(self.get_flag_C())     # 0xDA = JP C, a16
        self.instructions[0xE9] = self.JP_HL                                 # 0xE9 = JP HL

        # define additional LD operations
        self.instructions[0xE0] = lambda: self.LD_a8_X (self.A) # 0xE0 = LD (a8), A
        self.instructions[0xEA] = lambda: self.LD_a16_X(self.A) # 0xEA = LD (a16), A
        self.instructions[0xF0] = lambda: self.LD_X_a8 (self.A) # 0xF0 = LD A, (a8)
        self.instructions[0xFA] = lambda: self.LD_X_a16(self.A) # 0xFA = LD A, (a16)

        # define 0xCB?? instructions
        pass # TODO

    # get flags
    def get_flag_Z(self): # Zero
        return self.F.get_bit(7)
    def get_flag_N(self): # Subtract
        return self.F.get_bit(6)
    def get_flag_H(self): # Half-Carry
        return self.F.get_bit(5)
    def get_flag_C(self): # Carry
        return self.F.get_bit(4)

    # set flags to 1
    def set_flag_Z(self): # Zero
        self.F.set_bit(7)
    def set_flag_N(self): # Subtract
        self.F.set_bit(6)
    def set_flag_H(self): # Half-Carry
        self.F.set_bit(5)
    def set_flag_C(self): # Carry
        self.F.set_bit(4)

    # reset flags to 0
    def reset_flag_Z(self): # Zero
        self.F.reset_bit(7)
    def reset_flag_N(self): # Subtract
        self.F.reset_bit(6)
    def reset_flag_H(self): # Half-Carry
        self.F.reset_bit(5)
    def reset_flag_C(self): # Carry
        self.F.reset_bit(4)

    # read 8 bits (1 byte) after the PC
    def read_PC_8(self):
        return self.memory[self.PC.get() + 1]

    # read 16 bits (2 bytes) after the PC
    def read_PC_16(self):
        pc_orig = self.PC.get()
        return uint16(self.memory[pc_orig + 1] | (self.memory[pc_orig + 2] << 8))

    # 0x00
    def NOP(self):
        return 1, 1

    # 0x10
    def STOP(self):
        self.is_stopped = True
        return 2, 1

    # 0x37
    def SCF(self):
        self.reset_flag_N()
        self.reset_flag_H()
        self.set_flag_C()
        return 1, 1

    # 0x3F
    def CCF(self):
        self.reset_flag_N()
        self.reset_flag_H()
        if self.get_flag_C():
            self.reset_flag_C()
        else:
            self.set_flag_C()
        return 1, 1

    # 0x76
    def HALT(self):
        self.is_halted = True
        return 1, 1

    # 0xF3, 0xFB
    def set_IME(self, value):
        self.interrupt_master_enable = value
        return 1, 1

    # 0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E
    def LD_X_d8(self, register):
        register.set(self.read_PC_8())
        return 2, 2

    # 0x36
    def LD_addr_d8(self, register_target_address):
        self.memory[register_target_address.get()] = self.read_PC_8()
        return 2, 3

    # 0x01, 0x11, 0x21, 0x31
    def LD_XX_d16(self, register):
        register.set(self.read_PC_16())
        return 3, 3

    # 0xE0
    def LD_a8_X(self, register):
        self.memory[0xFF00 | self.read_PC_8()] = register.get()
        return 2, 3

    # 0xEA
    def LD_a16_X(self, register):
        self.memory[self.read_PC_16()] = register.get()
        return 3, 4

    # 0xF0
    def LD_X_a8(self, register):
        register.set(self.memory[0xFF00 | self.read_PC_8()])
        return 2, 3

    # 0xFF
    def LD_X_a16(self, register):
        register.set(self.memory[self.read_PC_16()])
        return 3, 4

    # 0x02, 0x12, 0x22, 0x32
    def LD_addr_X(self, register_source, register_target_address, register_target_delta=0):
        self.memory[register_target_address.get()] = register_source.get()
        if register_target_delta != 0:
            register_target_address.add(register_target_delta)
        return 1, 2

    # 0x03, 0x13, 0x23, 0x33
    def INC_XX(self, register):
        register.add(1)
        return 1, 2

    # 0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x3C
    def INC_X(self, register):
        result = (register.get() + 1) & 0xFF
        register.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        if (result & 0x0F) == 0:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        return 1, 1

    # 0x34
    def INC_addr(self, register_address):
        address = register_address.get()
        result = (self.memory[address] + 1) & 0xFF
        self.memory[address] = result
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        if (result & 0x0F) == 0:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        return 1, 3

    # 0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x3D
    def DEC_X(self, register):
        result = (register.get() + 255) & 0xFF # (X + 255) & 0xFF == (X - 1) & 0xFF
        register.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.set_flag_N()
        if (result & 0x0F) == 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        return 1, 1

    # 0x35
    def DEC_addr(self, register_address):
        address = register_address.get()
        result = (self.memory[address] + 255) & 0xFF # (X + 255) & 0xFF == (X - 1) & 0xFF
        self.memory[address] = result
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.set_flag_N()
        if (result & 0x0F) == 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        return 1, 3

    # 0x09, 0x19, 0x29, 0x39
    def ADD_XX_XX(self, register_store, register_other):
        rs_orig = register_store.get()
        ro_orig = register_other.get()
        result = rs_orig + ro_orig
        self.reset_flag_N()
        if ((rs_orig & 0x0FFF) + (ro_orig & 0x0FFF)) > 0x0FFF:
            self.reset_flag_H()
        else:
            self.set_flag_H()
        if result > 0xFFFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFFFF)
        return 1, 2

    # 0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8F
    def ADD_X_X(self, register_store, register_other, carry=False):
        rs_orig = register_store.get()
        ro_orig = register_other.get()
        result = rs_orig + ro_orig
        if carry and self.get_flag_C():
            result += 1
        if result == 0:
            self.set_flag_Z()
        else:
            result.reset_flag_Z()
        self.reset_flag_N()
        if ((rs_orig & 0x0F) + (ro_orig & 0x0F)) > 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if result > 0xFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFF)
        return 1, 1

    # 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9F
    def SUB_X_X(self, register_store, register_other, carry=False):
        rs_orig = int(register_store.get())
        ro_orig = int(register_other.get())
        result = rs_orig - ro_orig
        if carry and self.get_flag_C():
            result -= 1
        while result < 0:
            result += 0xFF
        if result == 0:
            self.set_flag_Z()
        else:
            result.reset_flag_Z()
        self.set_flag_N()
        if (rs_orig & 0x0F) < (ro_orig & 0x0F):
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if rs_orig < ro_orig:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFF)
        return 1, 1

    # 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAF
    def XOR(self, register_store, register_other):
        result = register_store.get() ^ register_other.get()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        return 1, 1

    # 0xAE
    def XOR_addr(self, register_store, register_other_address):
        result = register_store.get() ^ self.memory[register_other_address.get()]
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        return 1, 2

    # 0x18, 0x20, 0x28, 0x30, 0x38
    def JR_s8(self, condition):
        if condition:
            self.PC.add(2 + int8(self.read_PC_8()))
            return 0, 3 # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 2, 2

    # 0xC2, 0xC3, 0xCA, 0xD2, 0xDA
    def JP_a16(self, condition):
        if condition:
            self.PC.set(self.read_PC_16())
            return 0, 4 # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 3, 3

    # 0xE9
    def JP_HL(self):
        self.PC.set(self.HL.get())
        return 0, 1 # moves PC, so return 0 bytes (to not move PC again in emulation loop)

    # load a game
    def load_game(self, path):
        self.cartridge = load_game_data(path, ext='.gb')
        if self.cartridge[0x0147] != 0:
            raise NotImplementedError("Memory Bank Controllers (MBCs) are not implemented")
        self.memory[0x0000 : len(self.cartridge)] = memoryview(self.cartridge) # only supports "No MBC" (32 KiB ROM)

    # emulation loop
    def run(self):
        # set up pygame
        pygame.init()
        pygame.display.set_caption(self.cartridge[0x0134 : 0x0144].rstrip(b'\x00').decode('ascii', errors='replace').strip())
        window = pygame.display.set_mode((WIDTH*4, HEIGHT*4))
        surface = pygame.Surface((WIDTH, HEIGHT))
        surface.fill((0, 0, 0))
        clock = pygame.time.Clock()

        # set up sound
        pass # TODO

        #  run game
        running = True
        while running:
            # handle next key input
            for event in pygame.event.get():
                if (event.type == pygame.QUIT) or ((event.type == pygame.KEYDOWN) and (event.key == pygame.K_ESCAPE)):
                    running = False
                    break
            pressed = pygame.key.get_pressed()
            pass # TODO PARSE PRESSED KEYS

            # run 17,556 M-cycles
            m_cycles_remaining = 17556
            while m_cycles_remaining > 0:
                pc_orig = self.PC.get()
                opcode = self.memory[pc_orig]
                print(m_cycles_remaining, f'0x{opcode:02X}') # TODO
                if opcode == 0xCB:
                    cb_opcode = self.memory[pc_orig + 1]
                    try:
                        num_bytes, num_m_cycles = self.instructions[0xCB][cb_opcode]()
                    except:
                        raise ValueError(f"Unknown opcode: 0xCB{cb_opcode:02X}")
                else:
                    try:
                        num_bytes, num_m_cycles = self.instructions[opcode]()
                    except:
                        raise ValueError(f"Unknown opcode: 0x{opcode:02X}")
                self.PC.add(num_bytes)
                self.ppu.step(num_m_cycles)
                m_cycles_remaining -= num_m_cycles

            # update video
            self.ppu.render_frame(surface)
            pygame.transform.scale(surface, window.get_size(), window)
            pygame.display.flip()

            # update audio
            pass # TODO

            # maintain FPS
            clock.tick(FPS)

# run program
if __name__ == "__main__":
    from sys import argv
    if len(argv) != 2:
        raise ValueError("USAGE: %s <game_rom>" % argv[0])
    gb = GameBoy()
    gb.load_game(argv[1])
    gb.run()
