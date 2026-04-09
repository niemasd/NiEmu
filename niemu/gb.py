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
INTERRUPT_VECTORS = [
    (0x01, 0x0040), # VBlank
    (0x02, 0x0048), # LCD STAT
    (0x04, 0x0050), # Timer
    (0x08, 0x0058), # Serial
    (0x10, 0x0060), # Joypad
]

# class to represent Game Boy PPU
class PPU:
    # initialize a PPU object
    def __init__(self, memory):
        self.memory = memory
        self.scanline_m_cycles = 0
        self.memory[0xFF40] = 0x91  # LCDC: LCD on, BG on, tile data 0x8000, BG map 0x9800
        self.memory[0xFF41] = 0x85  # STAT
        self.memory[0xFF42] = 0x00  # SCY
        self.memory[0xFF43] = 0x00  # SCX
        self.memory[0xFF44] = 0x00  # LY
        self.memory[0xFF45] = 0x00  # LYC
        self.memory[0xFF47] = 0xFC  # BGP

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

    # advance LCD timing
    def step(self, m_cycles):
        # handle LCD not enabled
        if not self.lcd_enabled():
            self.scanline_m_cycles = 0
            self.memory[0xFF44] = 0
            self.memory[0xFF41] = (self.memory[0xFF41] & 0xFC) | 0
            return

        # 114 M-cycles per scanline
        self.scanline_m_cycles += m_cycles
        while self.scanline_m_cycles >= 114:
            self.scanline_m_cycles -= 114
            ly = (int(self.memory[0xFF44]) + 1) % 154
            self.memory[0xFF44] = ly
            if ly == 144:
                self.memory[0xFF0F] |= 0x01  # request VBlank interrupt

        # update STAT mode
        ly = int(self.memory[0xFF44])
        stat = int(self.memory[0xFF41]) & 0xFC
        if ly >= 144:
            mode = 1  # VBlank
        else:
            # rough visible-line timing split
            if self.scanline_m_cycles < 20:
                mode = 2  # OAM search
            elif self.scanline_m_cycles < 63:
                mode = 3  # drawing
            else:
                mode = 0  # HBlank
        stat |= mode

        # LYC == LY flag
        lyc = int(self.memory[0xFF45])
        if ly == lyc:
            stat |= 0x04
        else:
            stat &= 0xFB
        self.memory[0xFF41] = stat

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
        self.is_stopped = False

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
        self.memory[0xFF0F] = 0xE1 # IF
        self.memory[0xFFFF] = 0x00 # IE
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

        # define ADD ??, ?? instructions
        self.instructions[0x09] = lambda: self.ADD_XX_XX(self.HL, self.BC) # 0x09 = ADD HL, BC
        self.instructions[0x19] = lambda: self.ADD_XX_XX(self.HL, self.DE) # 0x19 = ADD HL, DE
        self.instructions[0x29] = lambda: self.ADD_XX_XX(self.HL, self.HL) # 0x29 = ADD HL, HL
        self.instructions[0x39] = lambda: self.ADD_XX_XX(self.HL, self.SP) # 0x39 = ADD HL, SP

        # define LD ?, (??) instructions
        self.instructions[0x0A] = lambda: self.LD_X_addr(self.BC, self.A,  0) # 0x0A = LD A, (BC)
        self.instructions[0x1A] = lambda: self.LD_X_addr(self.DE, self.A,  0) # 0x1A = LD A, (DE)
        self.instructions[0x2A] = lambda: self.LD_X_addr(self.HL, self.A,  1) # 0x2A = LD A, (HL+)
        self.instructions[0x3A] = lambda: self.LD_X_addr(self.HL, self.A, -1) # 0x3A = LD A, (HL-)
        self.instructions[0x46] = lambda: self.LD_X_addr(self.HL, self.B,  0) # 0x46 = LD B, (HL)
        self.instructions[0x4E] = lambda: self.LD_X_addr(self.HL, self.C,  0) # 0x4E = LD C, (HL)
        self.instructions[0x56] = lambda: self.LD_X_addr(self.HL, self.D,  0) # 0x56 = LD D, (HL)
        self.instructions[0x5E] = lambda: self.LD_X_addr(self.HL, self.E,  0) # 0x5E = LD E, (HL)
        self.instructions[0x66] = lambda: self.LD_X_addr(self.HL, self.H,  0) # 0x66 = LD H, (HL)
        self.instructions[0x6E] = lambda: self.LD_X_addr(self.HL, self.L,  0) # 0x6E = LD L, (HL)
        self.instructions[0x7E] = lambda: self.LD_X_addr(self.HL, self.A,  0) # 0x7E = LD A, (HL)

        # define DEC ?? instructions
        self.instructions[0x0B] = lambda: self.DEC_XX(self.BC) # 0x0B = DEC BC
        self.instructions[0x1B] = lambda: self.DEC_XX(self.DE) # 0x1B = DEC DE
        self.instructions[0x2B] = lambda: self.DEC_XX(self.HL) # 0x2B = DEC HL
        self.instructions[0x3B] = lambda: self.DEC_XX(self.SP) # 0x3B = DEC SP

        # define JR instructions
        self.instructions[0x18] = lambda: self.JR_s8(True)                  # 0x18 = JR s8
        self.instructions[0x20] = lambda: self.JR_s8(not self.get_flag_Z()) # 0x20 = JR NZ, s8
        self.instructions[0x28] = lambda: self.JR_s8(self.get_flag_Z())     # 0x28 = JR Z, s8
        self.instructions[0x30] = lambda: self.JR_s8(not self.get_flag_C()) # 0x30 = JR NC, s8
        self.instructions[0x38] = lambda: self.JR_s8(self.get_flag_C())     # 0x38 = JR C, s8

        # define ADD ?, ? instructions
        self.instructions[0x80] = lambda: self.ADD_X_X(self.A, self.B)             # 0x80 = ADD A, B
        self.instructions[0x81] = lambda: self.ADD_X_X(self.A, self.C)             # 0x81 = ADD A, C
        self.instructions[0x82] = lambda: self.ADD_X_X(self.A, self.D)             # 0x82 = ADD A, D
        self.instructions[0x83] = lambda: self.ADD_X_X(self.A, self.E)             # 0x83 = ADD A, E
        self.instructions[0x84] = lambda: self.ADD_X_X(self.A, self.H)             # 0x84 = ADD A, H
        self.instructions[0x85] = lambda: self.ADD_X_X(self.A, self.L)             # 0x85 = ADD A, L
        self.instructions[0x87] = lambda: self.ADD_X_X(self.A, self.A)             # 0x87 = ADD A, A
        self.instructions[0x86] = lambda: self.ADD_X_X(self.A, self.HL, addr=True) # 0x86 = ADD A, (HL)
        self.instructions[0xC6] = lambda: self.ADD_X_d8(self.A)                    # 0xC6 = ADD A, d8

        # define ADC ?, ? instructions
        self.instructions[0x88] = lambda: self.ADD_X_X(self.A, self.B, carry=True)             # 0x88 = ADC A, B
        self.instructions[0x89] = lambda: self.ADD_X_X(self.A, self.C, carry=True)             # 0x89 = ADC A, C
        self.instructions[0x8A] = lambda: self.ADD_X_X(self.A, self.D, carry=True)             # 0x8A = ADC A, D
        self.instructions[0x8B] = lambda: self.ADD_X_X(self.A, self.E, carry=True)             # 0x8B = ADC A, E
        self.instructions[0x8C] = lambda: self.ADD_X_X(self.A, self.H, carry=True)             # 0x8C = ADC A, H
        self.instructions[0x8D] = lambda: self.ADD_X_X(self.A, self.L, carry=True)             # 0x8D = ADC A, L
        self.instructions[0x8F] = lambda: self.ADD_X_X(self.A, self.A, carry=True)             # 0x8F = ADC A, A
        self.instructions[0x8E] = lambda: self.ADD_X_X(self.A, self.HL, addr=True, carry=True) # 0x8E = ADC A, (HL)
        self.instructions[0xCE] = lambda: self.ADD_X_d8(self.A, carry=True)                    # 0xCE = ADC A, d8

        # define SUB ?, ? instructions
        self.instructions[0x90] = lambda: self.SUB_X_X(self.A, self.B)             # 0x90 = SUB A, B
        self.instructions[0x91] = lambda: self.SUB_X_X(self.A, self.C)             # 0x91 = SUB A, C
        self.instructions[0x92] = lambda: self.SUB_X_X(self.A, self.D)             # 0x92 = SUB A, D
        self.instructions[0x93] = lambda: self.SUB_X_X(self.A, self.E)             # 0x93 = SUB A, E
        self.instructions[0x94] = lambda: self.SUB_X_X(self.A, self.H)             # 0x94 = SUB A, H
        self.instructions[0x95] = lambda: self.SUB_X_X(self.A, self.L)             # 0x95 = SUB A, L
        self.instructions[0x97] = lambda: self.SUB_X_X(self.A, self.A)             # 0x97 = SUB A, A
        self.instructions[0x96] = lambda: self.SUB_X_X(self.A, self.HL, addr=True) # 0x96 = SUB (HL)
        self.instructions[0xD6] = lambda: self.SUB_X_d8(self.A)                    # 0xD6 = SUB d8

        # define SBC ?, ? instructions
        self.instructions[0x98] = lambda: self.SUB_X_X(self.A, self.B, carry=True)             # 0x98 = SBC A, B
        self.instructions[0x99] = lambda: self.SUB_X_X(self.A, self.C, carry=True)             # 0x99 = SBC A, C
        self.instructions[0x9A] = lambda: self.SUB_X_X(self.A, self.D, carry=True)             # 0x9A = SBC A, D
        self.instructions[0x9B] = lambda: self.SUB_X_X(self.A, self.E, carry=True)             # 0x9B = SBC A, E
        self.instructions[0x9C] = lambda: self.SUB_X_X(self.A, self.H, carry=True)             # 0x9C = SBC A, H
        self.instructions[0x9D] = lambda: self.SUB_X_X(self.A, self.L, carry=True)             # 0x9D = SBC A, L
        self.instructions[0x9F] = lambda: self.SUB_X_X(self.A, self.A, carry=True)             # 0x9F = SBC A, A
        self.instructions[0x9E] = lambda: self.SUB_X_X(self.A, self.HL, addr=True, carry=True) # 0x9E = SBC A, (HL)
        self.instructions[0xDE] = lambda: self.SUB_X_d8(self.A, carry=True)                    # 0xDE = SBC A, d8

        # define AND ? instructions
        self.instructions[0xA0] = lambda: self.AND(self.A, self.B)             # 0xA0 = AND B
        self.instructions[0xA1] = lambda: self.AND(self.A, self.C)             # 0xA1 = AND C
        self.instructions[0xA2] = lambda: self.AND(self.A, self.D)             # 0xA2 = AND D
        self.instructions[0xA3] = lambda: self.AND(self.A, self.E)             # 0xA3 = AND E
        self.instructions[0xA4] = lambda: self.AND(self.A, self.H)             # 0xA4 = AND H
        self.instructions[0xA5] = lambda: self.AND(self.A, self.L)             # 0xA5 = AND L
        self.instructions[0xA7] = lambda: self.AND(self.A, self.A)             # 0xA7 = AND A
        self.instructions[0xA6] = lambda: self.AND(self.A, self.HL, addr=True) # 0xA6 = AND (HL)
        self.instructions[0xE6] = lambda: self.AND_d8(self.A)                  # 0xE6 = AND d8

        # define XOR ? instructions
        self.instructions[0xA8] = lambda: self.XOR(self.A, self.B)             # 0xA8 = XOR B
        self.instructions[0xA9] = lambda: self.XOR(self.A, self.C)             # 0xA9 = XOR C
        self.instructions[0xAA] = lambda: self.XOR(self.A, self.D)             # 0xAA = XOR D
        self.instructions[0xAB] = lambda: self.XOR(self.A, self.E)             # 0xAB = XOR E
        self.instructions[0xAC] = lambda: self.XOR(self.A, self.H)             # 0xAC = XOR H
        self.instructions[0xAD] = lambda: self.XOR(self.A, self.L)             # 0xAD = XOR L
        self.instructions[0xAF] = lambda: self.XOR(self.A, self.A)             # 0xAF = XOR A
        self.instructions[0xAE] = lambda: self.XOR(self.A, self.HL, addr=True) # 0xAE = XOR (HL)
        self.instructions[0xEE] = lambda: self.XOR_d8(self.A)                  # 0xEE = XOR d8

        # define OR ? instructions
        self.instructions[0xB0] = lambda: self.OR(self.A, self.B)             # 0xB0 = OR B
        self.instructions[0xB1] = lambda: self.OR(self.A, self.C)             # 0xB1 = OR C
        self.instructions[0xB2] = lambda: self.OR(self.A, self.D)             # 0xB2 = OR D
        self.instructions[0xB3] = lambda: self.OR(self.A, self.E)             # 0xB3 = OR E
        self.instructions[0xB4] = lambda: self.OR(self.A, self.H)             # 0xB4 = OR H
        self.instructions[0xB5] = lambda: self.OR(self.A, self.L)             # 0xB5 = OR L
        self.instructions[0xB7] = lambda: self.OR(self.A, self.A)             # 0xB7 = OR A
        self.instructions[0xB6] = lambda: self.OR(self.A, self.HL, addr=True) # 0xB6 = OR (HL)
        self.instructions[0xF6] = lambda: self.OR_d8(self.A)                  # 0xF6 = OR d8

        # define CP ? instructions
        self.instructions[0xB8] = lambda: self.SUB_X_X(self.A, self.B, store=False)             # 0xB8 = CP B
        self.instructions[0xB9] = lambda: self.SUB_X_X(self.A, self.C, store=False)             # 0xB9 = CP C
        self.instructions[0xBA] = lambda: self.SUB_X_X(self.A, self.D, store=False)             # 0xBA = CP D
        self.instructions[0xBB] = lambda: self.SUB_X_X(self.A, self.E, store=False)             # 0xBB = CP E
        self.instructions[0xBC] = lambda: self.SUB_X_X(self.A, self.H, store=False)             # 0xBC = CP H
        self.instructions[0xBD] = lambda: self.SUB_X_X(self.A, self.L, store=False)             # 0xBD = CP L
        self.instructions[0xBF] = lambda: self.SUB_X_X(self.A, self.A, store=False)             # 0xBF = CP A
        self.instructions[0xBE] = lambda: self.SUB_X_X(self.A, self.HL, store=False, addr=True) # 0xBE = CP (HL)
        self.instructions[0xFE] = lambda: self.SUB_X_d8(self.A, store=False)                    # 0xFE = CP d8

        # define JP instructions
        self.instructions[0xC2] = lambda: self.JP_a16(not self.get_flag_Z()) # 0xC2 = JP NZ, a16
        self.instructions[0xC3] = lambda: self.JP_a16(True)                  # 0xC3 = JP a16
        self.instructions[0xCA] = lambda: self.JP_a16(self.get_flag_Z())     # 0xCA = JP Z, a16
        self.instructions[0xD2] = lambda: self.JP_a16(not self.get_flag_C()) # 0xD2 = JP NC, a16
        self.instructions[0xDA] = lambda: self.JP_a16(self.get_flag_C())     # 0xDA = JP C, a16
        self.instructions[0xE9] = self.JP_HL                                 # 0xE9 = JP HL

        # define CALL instructions
        self.instructions[0xC4] = lambda: self.CALL_a16(not self.get_flag_Z()) # 0xC4 = CALL NZ, a16
        self.instructions[0xCC] = lambda: self.CALL_a16(self.get_flag_Z())     # 0xCC = CALL Z, a16
        self.instructions[0xCD] = lambda: self.CALL_a16(True)                  # 0xCD = CALL a16
        self.instructions[0xD4] = lambda: self.CALL_a16(not self.get_flag_C()) # 0xD4 = CALL NC, a16
        self.instructions[0xDC] = lambda: self.CALL_a16(self.get_flag_C())     # 0xDC = CALL C, a16

        # define additional LD operations
        self.instructions[0xE0] = lambda: self.LD_a8_X(self.A)                # 0xE0 = LD (a8), A
        self.instructions[0xE2] = lambda: self.LD_addr_X_FF00(self.A, self.C) # 0xE2 = LD (C), A
        self.instructions[0xEA] = lambda: self.LD_a16_X(self.A)               # 0xEA = LD (a16), A
        self.instructions[0xF0] = lambda: self.LD_X_a8(self.A)                # 0xF0 = LD A, (a8)
        self.instructions[0xF2] = lambda: self.LD_X_addr_FF00(self.C, self.A) # 0xF2 = LD A, (C)
        self.instructions[0xFA] = lambda: self.LD_X_a16(self.A)               # 0xFA = LD A, (a16)

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

    # push 16-bit value onto stack
    def push_16(self, value):
        sp = int(self.SP.get())
        sp = (sp - 1) & 0xFFFF
        self.memory[sp] = (value >> 8) & 0xFF
        sp = (sp - 1) & 0xFFFF
        self.memory[sp] = value & 0xFF
        self.SP.set(sp)

    # service one pending interrupt if possible
    def service_interrupts(self):
        interrupt_enable = int(self.memory[0xFFFF])
        interrupt_flags = int(self.memory[0xFF0F])
        pending = interrupt_enable & interrupt_flags & 0x1F
        if pending == 0:
            return 0

        # HALT exits as soon as an interrupt is pending, even if IME is off
        if self.is_halted:
            self.is_halted = False
        if not self.interrupt_master_enable:
            return 0
        for mask, vector in INTERRUPT_VECTORS:
            if pending & mask:
                self.interrupt_master_enable = False
                self.memory[0xFF0F] = interrupt_flags & (~mask & 0xFF)
                self.push_16(int(self.PC.get()))
                self.PC.set(vector)
                return 5  # interrupt servicing costs 5 M-cycles
        return 0

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

    # 0xFA
    def LD_X_a16(self, register):
        register.set(self.memory[self.read_PC_16()])
        return 3, 4

    # 0x02, 0x12, 0x22, 0x32
    def LD_addr_X(self, register_source, register_target_address, register_target_delta=0):
        self.memory[register_target_address.get()] = register_source.get()
        if register_target_delta != 0:
            register_target_address.add(register_target_delta)
        return 1, 2

    # 0xE2
    def LD_addr_X_FF00(self, register_source, register_target_address):
        self.memory[0xFF00 | register_target_address.get()] = register_source.get()
        return 1, 2

    # 0x0A, 0x1A, 0x2A, 0x3A
    def LD_X_addr(self, register_source_address, register_target, register_source_delta=0):
        register_target.set(self.memory[register_source_address.get()])
        if register_source_delta != 0:
            register_source_address.add(register_source_delta)
        return 1, 2

    # 0xF2
    def LD_X_addr_FF00(self, register_source_address, register_target):
        register_target.set(self.memory[0xFF00 | register_source_address.get()])
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

    # 0x0B, 0x1B, 0x2B, 0x3B
    def DEC_XX(self, register):
        register.add(-1)
        return 1, 2

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
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if result > 0xFFFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFFFF)
        return 1, 2

    # 0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F
    def ADD_X_X(self, register_store, register_other, addr=False, carry=False):
        rs_orig = register_store.get()
        ro_orig = register_other.get()
        c_orig = int(carry and self.get_flag_C())
        if addr:
            ro_orig = self.memory[ro_orig]
        result = rs_orig + ro_orig + c_orig
        if (result & 0xFF) == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        if ((rs_orig & 0x0F) + (ro_orig & 0x0F) + c_orig) > 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if result > 0xFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFF)
        if addr:
            return 1, 2
        else:
            return 1, 1

    # 0xC6, 0xCE
    def ADD_X_d8(self, register_store, carry=False):
        rs_orig = register_store.get()
        ro_orig = self.read_PC_8()
        c_orig = int(carry and self.get_flag_C())
        result = rs_orig + ro_orig + c_orig
        if (result & 0xFF) == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        if ((rs_orig & 0x0F) + (ro_orig & 0x0F) + c_orig) > 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if result > 0xFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(result & 0xFF)
        return 2, 2

    # 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F, 0xB8, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF
    def SUB_X_X(self, register_store, register_other, store=True, addr=False, carry=False):
        rs_orig = int(register_store.get())
        ro_orig = int(register_other.get())
        c_orig = int(carry and self.get_flag_C())
        if addr:
            ro_orig = int(self.memory[ro_orig])
        result = (rs_orig - ro_orig - c_orig) & 0xFF
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.set_flag_N()
        if (rs_orig & 0x0F) < ((ro_orig & 0x0F) + c_orig):
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if rs_orig < (ro_orig + c_orig):
            self.set_flag_C()
        else:
            self.reset_flag_C()
        if store:
            register_store.set(result & 0xFF)
        if addr:
            return 1, 2
        else:
            return 1, 1

    # 0xD6, 0xDE, 0xFE
    def SUB_X_d8(self, register_store, store=True, carry=False):
        rs_orig = int(register_store.get())
        ro_orig = int(self.read_PC_8())
        c_orig = int(carry and self.get_flag_C())
        result = (rs_orig - ro_orig - c_orig) & 0xFF
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.set_flag_N()
        if (rs_orig & 0x0F) < ((ro_orig & 0x0F) + c_orig):
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if rs_orig < (ro_orig + c_orig):
            self.set_flag_C()
        else:
            self.reset_flag_C()
        if store:
            register_store.set(result & 0xFF)
        return 2, 2

    # 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7
    def AND(self, register_store, register_other, addr=False):
        if addr:
            result = register_store.get() & self.memory[register_other.get()]
        else:
            result = register_store.get() & register_other.get()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.set_flag_H()
        self.reset_flag_C()
        if addr:
            return 1, 2
        else:
            return 1, 1

    # 0xE6
    def AND_d8(self, register_store):
        result = register_store.get() & self.read_PC_8()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.set_flag_H()
        self.reset_flag_C()
        return 2, 2

    # 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF
    def XOR(self, register_store, register_other, addr=False):
        if addr:
            result = register_store.get() ^ self.memory[register_other.get()]
        else:
            result = register_store.get() ^ register_other.get()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        if addr:
            return 1, 2
        else:
            return 1, 1

    # 0xEE
    def XOR_d8(self, register_store):
        result = register_store.get() ^ self.read_PC_8()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        return 2, 2

    # 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7
    def OR(self, register_store, register_other, addr=False):
        if addr:
            result = register_store.get() | self.memory[register_other.get()]
        else:
            result = register_store.get() | register_other.get()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        if addr:
            return 1, 2
        else:
            return 1, 1

    # 0xF6
    def OR_d8(self, register_store):
        result = register_store.get() | self.read_PC_8()
        register_store.set(result)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        return 2, 2

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

    # 0xC4, 0xCC, 0xCD, 0xD4, 0xDC
    def CALL_a16(self, condition):
        if condition:
            self.push_16(self.PC.get() + 3)
            self.PC.set(self.read_PC_16())
            return 0, 6 # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 3, 3

    # load a game
    def load_game(self, path):
        self.cartridge = load_game_data(path, ext='.gb')
        if self.cartridge[0x0147] != 0:
            raise NotImplementedError("Memory Bank Controllers (MBCs) are not implemented")
        self.memory[0x0000 : len(self.cartridge)] = memoryview(self.cartridge) # only supports "No MBC" (32 KiB ROM)
        self.memory[0xFFFF] = 0x01 # enable VBlank interrupt

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
                # handle interrupts
                interrupt_m_cycles = self.service_interrupts()
                if interrupt_m_cycles > 0:
                    self.ppu.step(interrupt_m_cycles)
                    m_cycles_remaining -= interrupt_m_cycles
                    continue

                # handle CPU halt
                if self.is_halted:
                    num_m_cycles = 1
                    self.ppu.step(num_m_cycles)
                    m_cycles_remaining -= num_m_cycles
                    continue

                # rest of logic
                pc_orig = self.PC.get()
                opcode = self.memory[pc_orig]
                if opcode == 0xCB:
                    cb_opcode = self.memory[pc_orig + 1]
                    try:
                        instruction_func = self.instructions[0xCB][cb_opcode]
                        assert instruction_func is not None
                    except:
                        raise ValueError(f"Unknown opcode: 0xCB{cb_opcode:02X}")
                else:
                    try:
                        instruction_func = self.instructions[opcode]
                        assert instruction_func is not None
                    except:
                        raise ValueError(f"Unknown opcode: 0x{opcode:02X}")
                num_bytes, num_m_cycles = instruction_func()
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
