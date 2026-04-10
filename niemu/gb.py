#! /usr/bin/env python3
'''
Nintendo Game Boy Emulator

https://gbdev.io/pandocs
https://meganesu.github.io/generate-gb-opcodes
https://rgbds.gbdev.io/docs/v1.0.1/gbz80.7
'''

# imports
from niemu.common import *
from numpy import int8, uint8, uint16
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

    # LCDC helpers
    def lcd_enabled(self):
        return bool(int(self.memory[0xFF40]) & 0x80)
    def window_tile_map_select(self):
        return bool(int(self.memory[0xFF40]) & 0x40)
    def window_enabled(self):
        return bool(int(self.memory[0xFF40]) & 0x20)
    def bg_window_tile_data_select(self):
        return bool(int(self.memory[0xFF40]) & 0x10)
    def bg_tile_map_select(self):
        return bool(int(self.memory[0xFF40]) & 0x08)
    def sprite_size_8x16(self):
        return bool(int(self.memory[0xFF40]) & 0x04)
    def sprites_enabled(self):
        return bool(int(self.memory[0xFF40]) & 0x02)
    def bg_enabled(self):
        return bool(int(self.memory[0xFF40]) & 0x01)

    # palette helpers
    def get_bg_palette_color(self, color_id):
        bgp = int(self.memory[0xFF47])
        shade = (bgp >> (color_id * 2)) & 0b11
        return COLOR_PALETTE[shade]
    def get_obj_palette_color(self, palette_num, color_id):
        palette_addr = 0xFF49 if palette_num else 0xFF48 
        obp = int(self.memory[palette_addr])
        shade = (obp >> (color_id * 2)) & 0b11
        return COLOR_PALETTE[shade]

    # tile helpers
    def get_tile_pixel(self, tile_addr, x, y):
        bit = 7 - x
        row_addr = tile_addr + (y * 2)
        low = int(self.memory[row_addr])
        high = int(self.memory[row_addr + 1])
        lo_bit = (low >> bit) & 1
        hi_bit = (high >> bit) & 1
        return (hi_bit << 1) | lo_bit
    def get_bg_window_tile_addr(self, tile_number):
        if self.bg_window_tile_data_select():
            return 0x8000 + (tile_number * 16)
        signed_tile_number = int(int8(tile_number))
        return 0x9000 + (signed_tile_number * 16)
    def get_sprite_tile_addr(self, tile_number):
        return 0x8000 + (tile_number * 16)

    # background/window pixel fetch
    def get_bg_color_id_at(self, screen_x, screen_y):
        if not self.bg_enabled():
            return 0
        scy = int(self.memory[0xFF42])
        scx = int(self.memory[0xFF43])
        world_y = (screen_y + scy) & 0xFF
        world_x = (screen_x + scx) & 0xFF
        tile_y = world_y // 8
        tile_x = world_x // 8
        pixel_y = world_y % 8
        pixel_x = world_x % 8
        bg_map_base = 0x9C00 if self.bg_tile_map_select() else 0x9800
        tile_map_index = tile_y * 32 + tile_x
        tile_number = int(self.memory[bg_map_base + tile_map_index])
        tile_addr = self.get_bg_window_tile_addr(tile_number)
        return self.get_tile_pixel(tile_addr, pixel_x, pixel_y)
    def get_window_color_id_at(self, screen_x, screen_y):
        if not self.window_enabled():
            return None
        wy = int(self.memory[0xFF4A])
        wx = int(self.memory[0xFF4B]) - 7
        if screen_y < wy or screen_x < wx:
            return None
        window_x = screen_x - wx
        window_y = screen_y - wy
        tile_y = window_y // 8
        tile_x = window_x // 8
        pixel_y = window_y % 8
        pixel_x = window_x % 8
        win_map_base = 0x9C00 if self.window_tile_map_select() else 0x9800
        tile_map_index = tile_y * 32 + tile_x
        tile_number = int(self.memory[win_map_base + tile_map_index])
        tile_addr = self.get_bg_window_tile_addr(tile_number)
        return self.get_tile_pixel(tile_addr, pixel_x, pixel_y)

    # full frame render
    def render_background_and_window(self, surface, bg_color_ids):
        if not self.lcd_enabled():
            surface.fill(COLOR_WHITE)
            for y in range(HEIGHT):
                for x in range(WIDTH):
                    bg_color_ids[y][x] = 0
            return
        for screen_y in range(HEIGHT):
            for screen_x in range(WIDTH):
                color_id = self.get_bg_color_id_at(screen_x, screen_y)
                win_color_id = self.get_window_color_id_at(screen_x, screen_y)
                if win_color_id is not None:
                    color_id = win_color_id
                bg_color_ids[screen_y][screen_x] = color_id
                surface.set_at((screen_x, screen_y), self.get_bg_palette_color(color_id))
    def render_sprites(self, surface, bg_color_ids):
        if not self.lcd_enabled():
            return
        if not self.sprites_enabled():
            return
        sprite_height = 16 if self.sprite_size_8x16() else 8
        for sprite_index in range(39, -1, -1):
            oam_addr = 0xFE00 + sprite_index * 4
            sprite_y = int(self.memory[oam_addr]) - 16
            sprite_x = int(self.memory[oam_addr + 1]) - 8
            tile_number = int(self.memory[oam_addr + 2])
            attrs = int(self.memory[oam_addr + 3])
            priority_behind_bg = bool(attrs & 0x80)
            y_flip = bool(attrs & 0x40)
            x_flip = bool(attrs & 0x20)
            palette_num = 1 if (attrs & 0x10) else 0
            if sprite_height == 16:
                tile_number &= 0xFE
            for local_y in range(sprite_height):
                screen_y = sprite_y + local_y
                if screen_y < 0 or screen_y >= HEIGHT:
                    continue
                sprite_pixel_y = (sprite_height - 1 - local_y) if y_flip else local_y
                if sprite_height == 16:
                    if sprite_pixel_y < 8:
                        tile_addr = self.get_sprite_tile_addr(tile_number)
                        tile_row_y = sprite_pixel_y
                    else:
                        tile_addr = self.get_sprite_tile_addr(tile_number + 1)
                        tile_row_y = sprite_pixel_y - 8
                else:
                    tile_addr = self.get_sprite_tile_addr(tile_number)
                    tile_row_y = sprite_pixel_y
                for local_x in range(8):
                    screen_x = sprite_x + local_x
                    if screen_x < 0 or screen_x >= WIDTH:
                        continue
                    sprite_pixel_x = (7 - local_x) if x_flip else local_x
                    color_id = self.get_tile_pixel(tile_addr, sprite_pixel_x, tile_row_y)
                    if color_id == 0: # OBJ color 0 is transparent
                        continue
                    if priority_behind_bg and bg_color_ids[screen_y][screen_x] != 0: # sprite hidden when BG/window color id is nonzero
                        continue
                    surface.set_at(
                        (screen_x, screen_y),
                        self.get_obj_palette_color(palette_num, color_id)
                    )
    def render_frame(self, surface):
        bg_color_ids = [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]
        self.render_background_and_window(surface, bg_color_ids)
        self.render_sprites(surface, bg_color_ids)

    # advance LCD timing
    def step(self, m_cycles):
        # handle LCD not enabled
        if not self.lcd_enabled():
            self.scanline_m_cycles = 0
            self.memory.raw_write(0xFF44, 0)
            self.memory.raw_write(0xFF41, (int(self.memory[0xFF41]) & 0xFC) | 0)
            return

        # 114 M-cycles per scanline
        self.scanline_m_cycles += m_cycles
        while self.scanline_m_cycles >= 114:
            self.scanline_m_cycles -= 114
            ly = (int(self.memory[0xFF44]) + 1) % 154
            self.memory.raw_write(0xFF44, ly)
            if ly == 144:
                self.memory.raw_write(0xFF0F, int(self.memory[0xFF0F]) | 0x01) # request VBlank interrupt

        # update STAT mode
        ly = int(self.memory[0xFF44])
        stat = int(self.memory[0xFF41]) & 0xFC
        if ly >= 144:
            mode = 1  # VBlank
        else:
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
        self.memory.raw_write(0xFF41, stat)

# class to represent F register
class RegisterF(Register8):
    def set(self, value):
        super().set(value & 0xF0) # keep lower 4 bits cleared

# class to represent Nintendo Game Boy specific memory
class MemoryGB(Memory):
    def raw_write(self, i, x):
        super().__setitem__(int(i) & 0xFFFF, int(x) & 0xFF)
    def __setitem__(self, i, x):
        # DEBUG TODO DELETE
        if i == 0xFF40:
            print(f"WRITE LCDC PC={int(gb.PC.get()):04X} value={x:02X}")
        if isinstance(i, slice):
            return super().__setitem__(i, x)
        i = int(i) & 0xFFFF
        x = int(x) & 0xFF
        if 0x0000 <= i <= 0x7FFF: # ROM area: ignore writes
            return
        if 0xE000 <= i <= 0xFDFF: # Echo RAM
            super().__setitem__(i, x)
            super().__setitem__(i - 0x2000, x)
            return
        if 0xC000 <= i <= 0xDDFF: # Writing to C000-DDFF should also update echo
            super().__setitem__(i, x)
            super().__setitem__(i + 0x2000, x)
            return
        if 0xFEA0 <= i <= 0xFEFF: # Unusable area
            return
        if i == 0xFF04: # DIV resets to 0 on write
            super().__setitem__(i, 0)
            return
        if i == 0xFF44: # LY resets to 0 on write
            super().__setitem__(i, 0)
            return
        if i == 0xFF46: # DMA
            super().__setitem__(i, x)
            source = x << 8
            self.data[0xFE00:0xFEA0] = self.data[source:source + 0xA0]
            return
        super().__setitem__(i, x)

# class to emulate Nintendo Game Boy
class GameBoy:
    # initialize a GameBoy object
    def __init__(self):
        # CPU flags
        self.ime = False
        self.enable_ime_after_next_instruction = False
        self.just_executed_ei = False
        self.is_halted = False
        #self.is_stopped = False
        self.halt_bug = False

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
        self.memory = MemoryGB(0x10000)
        self.ppu = PPU(self.memory)
        self.cartridge = None
        self.instructions = [None]*0x100
        self.instructions[0xCB] = [None]*0x100

        # post-boot memory defaults
        self.memory[0xFF00:0xFF08] = [ # 0xFF00-0xFF07 = joypad, serial, timer
            0xCF, # 0xFF00 = JOYP
            0x00, # 0xFF01 = SB
            0x7E, # 0xFF02 = SC
            0x00, # 0xFF03 = unused
            0xAB, # 0xFF04 = DIV
            0x00, # 0xFF05 = TIMA
            0x00, # 0xFF06 = TMA
            0xF8, # 0xFF07 = TAC
        ]
        self.memory[0xFF0F] = 0xE1 # 0xFF0F = interrupt flags
        self.memory[0xFF10:0xFF27] = [ # 0xFF10-0xFF26 = sound
            0x80, # 0xFF10 = NR10
            0xBF, # 0xFF11 = NR11
            0xF3, # 0xFF12 = NR12
            0x00, # 0xFF13 = NR13
            0xBF, # 0xFF14 = NR14
            0x3F, # 0xFF15 = unused/NR20
            0x3F, # 0xFF16 = NR21
            0x00, # 0xFF17 = NR22
            0x00, # 0xFF18 = NR23
            0xBF, # 0xFF19 = NR24
            0x7F, # 0xFF1A = NR30
            0xFF, # 0xFF1B = NR31
            0x9F, # 0xFF1C = NR32
            0x00, # 0xFF1D = NR33
            0xBF, # 0xFF1E = NR34
            0xFF, # 0xFF1F = unused/NR40
            0xFF, # 0xFF20 = NR41
            0x00, # 0xFF21 = NR42
            0x00, # 0xFF22 = NR43
            0xBF, # 0xFF23 = NR44
            0x77, # 0xFF24 = NR50
            0xF3, # 0xFF25 = NR51
            0xF1, # 0xFF26 = NR52
        ]
        self.memory[0xFF40:0xFF4C] = [ # 0xFF40-0xFF4B = LCD/PPU
            0x91, # 0xFF40 = LCDC
            0x85, # 0xFF41 = STAT
            0x00, # 0xFF42 = SCY
            0x00, # 0xFF43 = SCX
            0x00, # 0xFF44 = LY
            0x00, # 0xFF45 = LYC
            0x00, # 0xFF46 = DMA
            0xFC, # 0xFF47 = BGP
            0xFF, # 0xFF48 = OBP0
            0xFF, # 0xFF49 = OBP1
            0x00, # 0xFF4A = WY
            0x00, # 0xFF4B = WX
        ]
        self.memory[0xFFFF] = 0x00 # 0xFFFF = interrupt enable

        ### define single-byte opcode instructions ###
        # define special instructions
        self.instructions[0x00] = self.NOP  # 0x00 = NOP
        self.instructions[0x10] = self.STOP # 0x10 = STOP
        self.instructions[0x37] = self.SCF  # 0x37 = SCF
        self.instructions[0x3F] = self.CCF  # 0x3F = CCF
        self.instructions[0x76] = self.HALT # 0x76 = HALT
        self.instructions[0xF3] = self.DI   # 0xF3 = DI
        self.instructions[0xFB] = self.EI   # 0xFB = EI

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

        # define CPL instruction
        self.instructions[0x2F] = lambda: self.CPL(self.A) # 0x2F = CPL

        # define LD ?, ? instructions
        self.instructions[0x40] = lambda: self.LD_X_X(self.B, self.B) # 0x40 = LD B, B
        self.instructions[0x41] = lambda: self.LD_X_X(self.B, self.C) # 0x41 = LD B, C
        self.instructions[0x42] = lambda: self.LD_X_X(self.B, self.D) # 0x42 = LD B, D
        self.instructions[0x43] = lambda: self.LD_X_X(self.B, self.E) # 0x43 = LD B, E
        self.instructions[0x44] = lambda: self.LD_X_X(self.B, self.H) # 0x44 = LD B, H
        self.instructions[0x45] = lambda: self.LD_X_X(self.B, self.L) # 0x45 = LD B, L
        self.instructions[0x47] = lambda: self.LD_X_X(self.B, self.A) # 0x47 = LD B, A
        self.instructions[0x48] = lambda: self.LD_X_X(self.C, self.B) # 0x48 = LD C, B
        self.instructions[0x49] = lambda: self.LD_X_X(self.C, self.C) # 0x49 = LD C, C
        self.instructions[0x4A] = lambda: self.LD_X_X(self.C, self.D) # 0x4A = LD C, D
        self.instructions[0x4B] = lambda: self.LD_X_X(self.C, self.E) # 0x4B = LD C, E
        self.instructions[0x4C] = lambda: self.LD_X_X(self.C, self.H) # 0x4C = LD C, H
        self.instructions[0x4D] = lambda: self.LD_X_X(self.C, self.L) # 0x4D = LD C, L
        self.instructions[0x4F] = lambda: self.LD_X_X(self.C, self.A) # 0x4F = LD C, A
        self.instructions[0x50] = lambda: self.LD_X_X(self.D, self.B) # 0x50 = LD D, B
        self.instructions[0x51] = lambda: self.LD_X_X(self.D, self.C) # 0x51 = LD D, C
        self.instructions[0x52] = lambda: self.LD_X_X(self.D, self.D) # 0x52 = LD D, D
        self.instructions[0x53] = lambda: self.LD_X_X(self.D, self.E) # 0x53 = LD D, E
        self.instructions[0x54] = lambda: self.LD_X_X(self.D, self.H) # 0x54 = LD D, H
        self.instructions[0x55] = lambda: self.LD_X_X(self.D, self.L) # 0x55 = LD D, L
        self.instructions[0x57] = lambda: self.LD_X_X(self.D, self.A) # 0x57 = LD D, A
        self.instructions[0x58] = lambda: self.LD_X_X(self.E, self.B) # 0x58 = LD E, B
        self.instructions[0x59] = lambda: self.LD_X_X(self.E, self.C) # 0x59 = LD E, C
        self.instructions[0x5A] = lambda: self.LD_X_X(self.E, self.D) # 0x5A = LD E, D
        self.instructions[0x5B] = lambda: self.LD_X_X(self.E, self.E) # 0x5B = LD E, E
        self.instructions[0x5C] = lambda: self.LD_X_X(self.E, self.H) # 0x5C = LD E, H
        self.instructions[0x5D] = lambda: self.LD_X_X(self.E, self.L) # 0x5D = LD E, L
        self.instructions[0x5F] = lambda: self.LD_X_X(self.E, self.A) # 0x5F = LD E, A
        self.instructions[0x60] = lambda: self.LD_X_X(self.H, self.B) # 0x60 = LD H, B
        self.instructions[0x61] = lambda: self.LD_X_X(self.H, self.C) # 0x61 = LD H, C
        self.instructions[0x62] = lambda: self.LD_X_X(self.H, self.D) # 0x62 = LD H, D
        self.instructions[0x63] = lambda: self.LD_X_X(self.H, self.E) # 0x63 = LD H, E
        self.instructions[0x64] = lambda: self.LD_X_X(self.H, self.H) # 0x64 = LD H, H
        self.instructions[0x65] = lambda: self.LD_X_X(self.H, self.L) # 0x65 = LD H, L
        self.instructions[0x67] = lambda: self.LD_X_X(self.H, self.A) # 0x67 = LD H, A
        self.instructions[0x68] = lambda: self.LD_X_X(self.L, self.B) # 0x68 = LD L, B
        self.instructions[0x69] = lambda: self.LD_X_X(self.L, self.C) # 0x69 = LD L, C
        self.instructions[0x6A] = lambda: self.LD_X_X(self.L, self.D) # 0x6A = LD L, D
        self.instructions[0x6B] = lambda: self.LD_X_X(self.L, self.E) # 0x6B = LD L, E
        self.instructions[0x6C] = lambda: self.LD_X_X(self.L, self.H) # 0x6C = LD L, H
        self.instructions[0x6D] = lambda: self.LD_X_X(self.L, self.L) # 0x6D = LD L, L
        self.instructions[0x6F] = lambda: self.LD_X_X(self.L, self.A) # 0x6F = LD L, A
        self.instructions[0x78] = lambda: self.LD_X_X(self.A, self.B) # 0x78 = LD A, B
        self.instructions[0x79] = lambda: self.LD_X_X(self.A, self.C) # 0x79 = LD A, C
        self.instructions[0x7A] = lambda: self.LD_X_X(self.A, self.D) # 0x7A = LD A, D
        self.instructions[0x7B] = lambda: self.LD_X_X(self.A, self.E) # 0x7B = LD A, E
        self.instructions[0x7C] = lambda: self.LD_X_X(self.A, self.H) # 0x7C = LD A, H
        self.instructions[0x7D] = lambda: self.LD_X_X(self.A, self.L) # 0x7D = LD A, L
        self.instructions[0x7F] = lambda: self.LD_X_X(self.A, self.A) # 0x7F = LD A, A

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

        # define RET instructions
        self.instructions[0xC0] = lambda: self.RET(not self.get_flag_Z())        # 0xC0 = RET NZ
        self.instructions[0xC8] = lambda: self.RET(self.get_flag_Z())            # 0xC8 = RET Z
        self.instructions[0xC9] = lambda: self.RET(True, num_cycles=4)           # 0xC9 = RET
        self.instructions[0xD0] = lambda: self.RET(not self.get_flag_C())        # 0xD0 = RET NC
        self.instructions[0xD8] = lambda: self.RET(self.get_flag_C())            # 0xD8 = RET C
        self.instructions[0xD9] = lambda: self.RET(True, num_cycles=4, ime=True) # 0xD9 = RETI

        # define POP instructions
        self.instructions[0xC1] = lambda: self.POP(self.BC) # 0xC1 = POP BC
        self.instructions[0xD1] = lambda: self.POP(self.DE) # 0xD1 = POP DE
        self.instructions[0xE1] = lambda: self.POP(self.HL) # 0xE1 = POP HL
        self.instructions[0xF1] = lambda: self.POP(self.AF) # 0xF1 = POP AF

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

        # define PUSH instructions
        self.instructions[0xC5] = lambda: self.PUSH(int(self.BC.get())) # 0xC5 = PUSH BC
        self.instructions[0xD5] = lambda: self.PUSH(int(self.DE.get())) # 0xD5 = PUSH DE
        self.instructions[0xE5] = lambda: self.PUSH(int(self.HL.get())) # 0xE5 = PUSH HL
        self.instructions[0xF5] = lambda: self.PUSH(int(self.AF.get())) # 0xF5 = PUSH AF

        # define RST instructions
        self.instructions[0xC7] = lambda: self.RST(0x00) # 0xC7 = RST 00H
        self.instructions[0xCF] = lambda: self.RST(0x08) # 0xCF = RST 08H
        self.instructions[0xD7] = lambda: self.RST(0x10) # 0xD7 = RST 10H
        self.instructions[0xDF] = lambda: self.RST(0x18) # 0xDF = RST 18H
        self.instructions[0xE7] = lambda: self.RST(0x20) # 0xE7 = RST 20H
        self.instructions[0xEF] = lambda: self.RST(0x28) # 0xEF = RST 28H
        self.instructions[0xF7] = lambda: self.RST(0x30) # 0xF7 = RST 30H
        self.instructions[0xFF] = lambda: self.RST(0x38) # 0xFF = RST 38H

        # define additional LD instructions
        self.instructions[0xE0] = lambda: self.LD_a8_X(self.A)                # 0xE0 = LD (a8), A
        self.instructions[0xE2] = lambda: self.LD_addr_X_FF00(self.A, self.C) # 0xE2 = LD (C), A
        self.instructions[0xEA] = lambda: self.LD_a16_X(self.A)               # 0xEA = LD (a16), A
        self.instructions[0xF0] = lambda: self.LD_X_a8(self.A)                # 0xF0 = LD A, (a8)
        self.instructions[0xF2] = lambda: self.LD_X_addr_FF00(self.C, self.A) # 0xF2 = LD A, (C)
        self.instructions[0xF8] = lambda: self.LD_XX_XX_s8(self.HL, self.SP)  # 0xF8 = LD HL, SP+s8
        self.instructions[0xFA] = lambda: self.LD_X_a16(self.A)               # 0xFA = LD A, (a16)

        ### define 0xCB?? instructions ###
        # define SWAP instructions
        self.instructions[0xCB][0x30] = lambda: self.SWAP_X(self.B)     # 0xCB30 = SWAP B
        self.instructions[0xCB][0x31] = lambda: self.SWAP_X(self.C)     # 0xCB31 = SWAP C
        self.instructions[0xCB][0x32] = lambda: self.SWAP_X(self.D)     # 0xCB32 = SWAP D
        self.instructions[0xCB][0x33] = lambda: self.SWAP_X(self.E)     # 0xCB33 = SWAP E
        self.instructions[0xCB][0x34] = lambda: self.SWAP_X(self.H)     # 0xCB34 = SWAP H
        self.instructions[0xCB][0x35] = lambda: self.SWAP_X(self.L)     # 0xCB35 = SWAP L
        self.instructions[0xCB][0x37] = lambda: self.SWAP_X(self.A)     # 0xCB37 = SWAP A
        self.instructions[0xCB][0x36] = lambda: self.SWAP_addr(self.HL) # 0xCB36 = SWAP (HL)

        # define BIT ?, ? instructions
        self.instructions[0xCB][0x40] = lambda: self.BIT(self.B, 0) # 0xCB40 = BIT 0, B
        self.instructions[0xCB][0x41] = lambda: self.BIT(self.C, 0) # 0xCB41 = BIT 0, C
        self.instructions[0xCB][0x42] = lambda: self.BIT(self.D, 0) # 0xCB42 = BIT 0, D
        self.instructions[0xCB][0x43] = lambda: self.BIT(self.E, 0) # 0xCB43 = BIT 0, E
        self.instructions[0xCB][0x44] = lambda: self.BIT(self.H, 0) # 0xCB44 = BIT 0, H
        self.instructions[0xCB][0x45] = lambda: self.BIT(self.L, 0) # 0xCB45 = BIT 0, L
        self.instructions[0xCB][0x47] = lambda: self.BIT(self.A, 0) # 0xCB47 = BIT 0, A
        self.instructions[0xCB][0x48] = lambda: self.BIT(self.B, 1) # 0xCB48 = BIT 1, B
        self.instructions[0xCB][0x49] = lambda: self.BIT(self.C, 1) # 0xCB49 = BIT 1, C
        self.instructions[0xCB][0x4A] = lambda: self.BIT(self.D, 1) # 0xCB4A = BIT 1, D
        self.instructions[0xCB][0x4B] = lambda: self.BIT(self.E, 1) # 0xCB4B = BIT 1, E
        self.instructions[0xCB][0x4C] = lambda: self.BIT(self.H, 1) # 0xCB4C = BIT 1, H
        self.instructions[0xCB][0x4D] = lambda: self.BIT(self.L, 1) # 0xCB4D = BIT 1, L
        self.instructions[0xCB][0x4F] = lambda: self.BIT(self.A, 1) # 0xCB4F = BIT 1, A
        self.instructions[0xCB][0x50] = lambda: self.BIT(self.B, 2) # 0xCB50 = BIT 2, B
        self.instructions[0xCB][0x51] = lambda: self.BIT(self.C, 2) # 0xCB51 = BIT 2, C
        self.instructions[0xCB][0x52] = lambda: self.BIT(self.D, 2) # 0xCB52 = BIT 2, D
        self.instructions[0xCB][0x53] = lambda: self.BIT(self.E, 2) # 0xCB53 = BIT 2, E
        self.instructions[0xCB][0x54] = lambda: self.BIT(self.H, 2) # 0xCB54 = BIT 2, H
        self.instructions[0xCB][0x55] = lambda: self.BIT(self.L, 2) # 0xCB55 = BIT 2, L
        self.instructions[0xCB][0x57] = lambda: self.BIT(self.A, 2) # 0xCB57 = BIT 2, A
        self.instructions[0xCB][0x58] = lambda: self.BIT(self.B, 3) # 0xCB58 = BIT 3, B
        self.instructions[0xCB][0x59] = lambda: self.BIT(self.C, 3) # 0xCB59 = BIT 3, C
        self.instructions[0xCB][0x5A] = lambda: self.BIT(self.D, 3) # 0xCB5A = BIT 3, D
        self.instructions[0xCB][0x5B] = lambda: self.BIT(self.E, 3) # 0xCB5B = BIT 3, E
        self.instructions[0xCB][0x5C] = lambda: self.BIT(self.H, 3) # 0xCB5C = BIT 3, H
        self.instructions[0xCB][0x5D] = lambda: self.BIT(self.L, 3) # 0xCB5D = BIT 3, L
        self.instructions[0xCB][0x5F] = lambda: self.BIT(self.A, 3) # 0xCB5F = BIT 3, A
        self.instructions[0xCB][0x60] = lambda: self.BIT(self.B, 4) # 0xCB60 = BIT 4, B
        self.instructions[0xCB][0x61] = lambda: self.BIT(self.C, 4) # 0xCB61 = BIT 4, C
        self.instructions[0xCB][0x62] = lambda: self.BIT(self.D, 4) # 0xCB62 = BIT 4, D
        self.instructions[0xCB][0x63] = lambda: self.BIT(self.E, 4) # 0xCB63 = BIT 4, E
        self.instructions[0xCB][0x64] = lambda: self.BIT(self.H, 4) # 0xCB64 = BIT 4, H
        self.instructions[0xCB][0x65] = lambda: self.BIT(self.L, 4) # 0xCB65 = BIT 4, L
        self.instructions[0xCB][0x67] = lambda: self.BIT(self.A, 4) # 0xCB67 = BIT 4, A
        self.instructions[0xCB][0x68] = lambda: self.BIT(self.B, 5) # 0xCB68 = BIT 5, B
        self.instructions[0xCB][0x69] = lambda: self.BIT(self.C, 5) # 0xCB69 = BIT 5, C
        self.instructions[0xCB][0x6A] = lambda: self.BIT(self.D, 5) # 0xCB6A = BIT 5, D
        self.instructions[0xCB][0x6B] = lambda: self.BIT(self.E, 5) # 0xCB6B = BIT 5, E
        self.instructions[0xCB][0x6C] = lambda: self.BIT(self.H, 5) # 0xCB6C = BIT 5, H
        self.instructions[0xCB][0x6D] = lambda: self.BIT(self.L, 5) # 0xCB6D = BIT 5, L
        self.instructions[0xCB][0x6F] = lambda: self.BIT(self.A, 5) # 0xCB6F = BIT 5, A
        self.instructions[0xCB][0x70] = lambda: self.BIT(self.B, 6) # 0xCB70 = BIT 6, B
        self.instructions[0xCB][0x71] = lambda: self.BIT(self.C, 6) # 0xCB71 = BIT 6, C
        self.instructions[0xCB][0x72] = lambda: self.BIT(self.D, 6) # 0xCB72 = BIT 6, D
        self.instructions[0xCB][0x73] = lambda: self.BIT(self.E, 6) # 0xCB73 = BIT 6, E
        self.instructions[0xCB][0x74] = lambda: self.BIT(self.H, 6) # 0xCB74 = BIT 6, H
        self.instructions[0xCB][0x75] = lambda: self.BIT(self.L, 6) # 0xCB75 = BIT 6, L
        self.instructions[0xCB][0x77] = lambda: self.BIT(self.A, 6) # 0xCB77 = BIT 6, A
        self.instructions[0xCB][0x78] = lambda: self.BIT(self.B, 7) # 0xCB78 = BIT 7, B
        self.instructions[0xCB][0x79] = lambda: self.BIT(self.C, 7) # 0xCB79 = BIT 7, C
        self.instructions[0xCB][0x7A] = lambda: self.BIT(self.D, 7) # 0xCB7A = BIT 7, D
        self.instructions[0xCB][0x7B] = lambda: self.BIT(self.E, 7) # 0xCB7B = BIT 7, E
        self.instructions[0xCB][0x7C] = lambda: self.BIT(self.H, 7) # 0xCB7C = BIT 7, H
        self.instructions[0xCB][0x7D] = lambda: self.BIT(self.L, 7) # 0xCB7D = BIT 7, L
        self.instructions[0xCB][0x7F] = lambda: self.BIT(self.A, 7) # 0xCB7F = BIT 7, A

        # define BIT ?, (??) instructions
        self.instructions[0xCB][0x46] = lambda: self.BIT_addr(self.HL, 0) # 0xCB46 = BIT 0, (HL)
        self.instructions[0xCB][0x4E] = lambda: self.BIT_addr(self.HL, 1) # 0xCB4E = BIT 1, (HL)
        self.instructions[0xCB][0x56] = lambda: self.BIT_addr(self.HL, 2) # 0xCB56 = BIT 2, (HL)
        self.instructions[0xCB][0x5E] = lambda: self.BIT_addr(self.HL, 3) # 0xCB5E = BIT 3, (HL)
        self.instructions[0xCB][0x66] = lambda: self.BIT_addr(self.HL, 4) # 0xCB66 = BIT 4, (HL)
        self.instructions[0xCB][0x6E] = lambda: self.BIT_addr(self.HL, 5) # 0xCB6E = BIT 5, (HL)
        self.instructions[0xCB][0x76] = lambda: self.BIT_addr(self.HL, 6) # 0xCB76 = BIT 6, (HL)
        self.instructions[0xCB][0x7E] = lambda: self.BIT_addr(self.HL, 7) # 0xCB7E = BIT 7, (HL)

        # define RES ?, ? instructions
        self.instructions[0xCB][0x80] = lambda: self.RES(self.B, 0) # 0xCB80 = RES 0, B
        self.instructions[0xCB][0x81] = lambda: self.RES(self.C, 0) # 0xCB81 = RES 0, C
        self.instructions[0xCB][0x82] = lambda: self.RES(self.D, 0) # 0xCB82 = RES 0, D
        self.instructions[0xCB][0x83] = lambda: self.RES(self.E, 0) # 0xCB83 = RES 0, E
        self.instructions[0xCB][0x84] = lambda: self.RES(self.H, 0) # 0xCB84 = RES 0, H
        self.instructions[0xCB][0x85] = lambda: self.RES(self.L, 0) # 0xCB85 = RES 0, L
        self.instructions[0xCB][0x87] = lambda: self.RES(self.A, 0) # 0xCB87 = RES 0, A
        self.instructions[0xCB][0x88] = lambda: self.RES(self.B, 1) # 0xCB88 = RES 1, B
        self.instructions[0xCB][0x89] = lambda: self.RES(self.C, 1) # 0xCB89 = RES 1, C
        self.instructions[0xCB][0x8A] = lambda: self.RES(self.D, 1) # 0xCB8A = RES 1, D
        self.instructions[0xCB][0x8B] = lambda: self.RES(self.E, 1) # 0xCB8B = RES 1, E
        self.instructions[0xCB][0x8C] = lambda: self.RES(self.H, 1) # 0xCB8C = RES 1, H
        self.instructions[0xCB][0x8D] = lambda: self.RES(self.L, 1) # 0xCB8D = RES 1, L
        self.instructions[0xCB][0x8F] = lambda: self.RES(self.A, 1) # 0xCB8F = RES 1, A
        self.instructions[0xCB][0x90] = lambda: self.RES(self.B, 2) # 0xCB90 = RES 2, B
        self.instructions[0xCB][0x91] = lambda: self.RES(self.C, 2) # 0xCB91 = RES 2, C
        self.instructions[0xCB][0x92] = lambda: self.RES(self.D, 2) # 0xCB92 = RES 2, D
        self.instructions[0xCB][0x93] = lambda: self.RES(self.E, 2) # 0xCB93 = RES 2, E
        self.instructions[0xCB][0x94] = lambda: self.RES(self.H, 2) # 0xCB94 = RES 2, H
        self.instructions[0xCB][0x95] = lambda: self.RES(self.L, 2) # 0xCB95 = RES 2, L
        self.instructions[0xCB][0x97] = lambda: self.RES(self.A, 2) # 0xCB97 = RES 2, A
        self.instructions[0xCB][0x98] = lambda: self.RES(self.B, 3) # 0xCB98 = RES 3, B
        self.instructions[0xCB][0x99] = lambda: self.RES(self.C, 3) # 0xCB99 = RES 3, C
        self.instructions[0xCB][0x9A] = lambda: self.RES(self.D, 3) # 0xCB9A = RES 3, D
        self.instructions[0xCB][0x9B] = lambda: self.RES(self.E, 3) # 0xCB9B = RES 3, E
        self.instructions[0xCB][0x9C] = lambda: self.RES(self.H, 3) # 0xCB9C = RES 3, H
        self.instructions[0xCB][0x9D] = lambda: self.RES(self.L, 3) # 0xCB9D = RES 3, L
        self.instructions[0xCB][0x9F] = lambda: self.RES(self.A, 3) # 0xCB9F = RES 3, A
        self.instructions[0xCB][0xA0] = lambda: self.RES(self.B, 4) # 0xCBA0 = RES 4, B
        self.instructions[0xCB][0xA1] = lambda: self.RES(self.C, 4) # 0xCBA1 = RES 4, C
        self.instructions[0xCB][0xA2] = lambda: self.RES(self.D, 4) # 0xCBA2 = RES 4, D
        self.instructions[0xCB][0xA3] = lambda: self.RES(self.E, 4) # 0xCBA3 = RES 4, E
        self.instructions[0xCB][0xA4] = lambda: self.RES(self.H, 4) # 0xCBA4 = RES 4, H
        self.instructions[0xCB][0xA5] = lambda: self.RES(self.L, 4) # 0xCBA5 = RES 4, L
        self.instructions[0xCB][0xA7] = lambda: self.RES(self.A, 4) # 0xCBA7 = RES 4, A
        self.instructions[0xCB][0xA8] = lambda: self.RES(self.B, 5) # 0xCBA8 = RES 5, B
        self.instructions[0xCB][0xA9] = lambda: self.RES(self.C, 5) # 0xCBA9 = RES 5, C
        self.instructions[0xCB][0xAA] = lambda: self.RES(self.D, 5) # 0xCBAA = RES 5, D
        self.instructions[0xCB][0xAB] = lambda: self.RES(self.E, 5) # 0xCBAB = RES 5, E
        self.instructions[0xCB][0xAC] = lambda: self.RES(self.H, 5) # 0xCBAC = RES 5, H
        self.instructions[0xCB][0xAD] = lambda: self.RES(self.L, 5) # 0xCBAD = RES 5, L
        self.instructions[0xCB][0xAF] = lambda: self.RES(self.A, 5) # 0xCBAF = RES 5, A
        self.instructions[0xCB][0xB0] = lambda: self.RES(self.B, 6) # 0xCBB0 = RES 6, B
        self.instructions[0xCB][0xB1] = lambda: self.RES(self.C, 6) # 0xCBB1 = RES 6, C
        self.instructions[0xCB][0xB2] = lambda: self.RES(self.D, 6) # 0xCBB2 = RES 6, D
        self.instructions[0xCB][0xB3] = lambda: self.RES(self.E, 6) # 0xCBB3 = RES 6, E
        self.instructions[0xCB][0xB4] = lambda: self.RES(self.H, 6) # 0xCBB4 = RES 6, H
        self.instructions[0xCB][0xB5] = lambda: self.RES(self.L, 6) # 0xCBB5 = RES 6, L
        self.instructions[0xCB][0xB7] = lambda: self.RES(self.A, 6) # 0xCBB7 = RES 6, A
        self.instructions[0xCB][0xB8] = lambda: self.RES(self.B, 7) # 0xCBB8 = RES 7, B
        self.instructions[0xCB][0xB9] = lambda: self.RES(self.C, 7) # 0xCBB9 = RES 7, C
        self.instructions[0xCB][0xBA] = lambda: self.RES(self.D, 7) # 0xCBBA = RES 7, D
        self.instructions[0xCB][0xBB] = lambda: self.RES(self.E, 7) # 0xCBBB = RES 7, E
        self.instructions[0xCB][0xBC] = lambda: self.RES(self.H, 7) # 0xCBBC = RES 7, H
        self.instructions[0xCB][0xBD] = lambda: self.RES(self.L, 7) # 0xCBBD = RES 7, L
        self.instructions[0xCB][0xBF] = lambda: self.RES(self.A, 7) # 0xCBBF = RES 7, A

        # define RES ?, (??) instructions
        self.instructions[0xCB][0x86] = lambda: self.RES_addr(self.HL, 0) # 0xCB86 = RES 0, (HL)
        self.instructions[0xCB][0x8E] = lambda: self.RES_addr(self.HL, 1) # 0xCB8E = RES 1, (HL)
        self.instructions[0xCB][0x96] = lambda: self.RES_addr(self.HL, 2) # 0xCB96 = RES 2, (HL)
        self.instructions[0xCB][0x9E] = lambda: self.RES_addr(self.HL, 3) # 0xCB9E = RES 3, (HL)
        self.instructions[0xCB][0xA6] = lambda: self.RES_addr(self.HL, 4) # 0xCBA6 = RES 4, (HL)
        self.instructions[0xCB][0xAE] = lambda: self.RES_addr(self.HL, 5) # 0xCBAE = RES 5, (HL)
        self.instructions[0xCB][0xB6] = lambda: self.RES_addr(self.HL, 6) # 0xCBB6 = RES 6, (HL)
        self.instructions[0xCB][0xBE] = lambda: self.RES_addr(self.HL, 7) # 0xCBBE = RES 7, (HL)

        # define SET ?, ? instructions
        self.instructions[0xCB][0xC0] = lambda: self.SET(self.B, 0) # 0xCBC0 = SET 0, B
        self.instructions[0xCB][0xC1] = lambda: self.SET(self.C, 0) # 0xCBC1 = SET 0, C
        self.instructions[0xCB][0xC2] = lambda: self.SET(self.D, 0) # 0xCBC2 = SET 0, D
        self.instructions[0xCB][0xC3] = lambda: self.SET(self.E, 0) # 0xCBC3 = SET 0, E
        self.instructions[0xCB][0xC4] = lambda: self.SET(self.H, 0) # 0xCBC4 = SET 0, H
        self.instructions[0xCB][0xC5] = lambda: self.SET(self.L, 0) # 0xCBC5 = SET 0, L
        self.instructions[0xCB][0xC7] = lambda: self.SET(self.A, 0) # 0xCBC7 = SET 0, A
        self.instructions[0xCB][0xC8] = lambda: self.SET(self.B, 1) # 0xCBC8 = SET 1, B
        self.instructions[0xCB][0xC9] = lambda: self.SET(self.C, 1) # 0xCBC9 = SET 1, C
        self.instructions[0xCB][0xCA] = lambda: self.SET(self.D, 1) # 0xCBCA = SET 1, D
        self.instructions[0xCB][0xCB] = lambda: self.SET(self.E, 1) # 0xCBCB = SET 1, E
        self.instructions[0xCB][0xCC] = lambda: self.SET(self.H, 1) # 0xCBCC = SET 1, H
        self.instructions[0xCB][0xCD] = lambda: self.SET(self.L, 1) # 0xCBCD = SET 1, L
        self.instructions[0xCB][0xCF] = lambda: self.SET(self.A, 1) # 0xCBCF = SET 1, A
        self.instructions[0xCB][0xD0] = lambda: self.SET(self.B, 2) # 0xCBD0 = SET 2, B
        self.instructions[0xCB][0xD1] = lambda: self.SET(self.C, 2) # 0xCBD1 = SET 2, C
        self.instructions[0xCB][0xD2] = lambda: self.SET(self.D, 2) # 0xCBD2 = SET 2, D
        self.instructions[0xCB][0xD3] = lambda: self.SET(self.E, 2) # 0xCBD3 = SET 2, E
        self.instructions[0xCB][0xD4] = lambda: self.SET(self.H, 2) # 0xCBD4 = SET 2, H
        self.instructions[0xCB][0xD5] = lambda: self.SET(self.L, 2) # 0xCBD5 = SET 2, L
        self.instructions[0xCB][0xD7] = lambda: self.SET(self.A, 2) # 0xCBD7 = SET 2, A
        self.instructions[0xCB][0xD8] = lambda: self.SET(self.B, 3) # 0xCBD8 = SET 3, B
        self.instructions[0xCB][0xD9] = lambda: self.SET(self.C, 3) # 0xCBD9 = SET 3, C
        self.instructions[0xCB][0xDA] = lambda: self.SET(self.D, 3) # 0xCBDA = SET 3, D
        self.instructions[0xCB][0xDB] = lambda: self.SET(self.E, 3) # 0xCBDB = SET 3, E
        self.instructions[0xCB][0xDC] = lambda: self.SET(self.H, 3) # 0xCBDC = SET 3, H
        self.instructions[0xCB][0xDD] = lambda: self.SET(self.L, 3) # 0xCBDD = SET 3, L
        self.instructions[0xCB][0xDF] = lambda: self.SET(self.A, 3) # 0xCBDF = SET 3, A
        self.instructions[0xCB][0xE0] = lambda: self.SET(self.B, 4) # 0xCBE0 = SET 4, B
        self.instructions[0xCB][0xE1] = lambda: self.SET(self.C, 4) # 0xCBE1 = SET 4, C
        self.instructions[0xCB][0xE2] = lambda: self.SET(self.D, 4) # 0xCBE2 = SET 4, D
        self.instructions[0xCB][0xE3] = lambda: self.SET(self.E, 4) # 0xCBE3 = SET 4, E
        self.instructions[0xCB][0xE4] = lambda: self.SET(self.H, 4) # 0xCBE4 = SET 4, H
        self.instructions[0xCB][0xE5] = lambda: self.SET(self.L, 4) # 0xCBE5 = SET 4, L
        self.instructions[0xCB][0xE7] = lambda: self.SET(self.A, 4) # 0xCBE7 = SET 4, A
        self.instructions[0xCB][0xE8] = lambda: self.SET(self.B, 5) # 0xCBE8 = SET 5, B
        self.instructions[0xCB][0xE9] = lambda: self.SET(self.C, 5) # 0xCBE9 = SET 5, C
        self.instructions[0xCB][0xEA] = lambda: self.SET(self.D, 5) # 0xCBEA = SET 5, D
        self.instructions[0xCB][0xEB] = lambda: self.SET(self.E, 5) # 0xCBEB = SET 5, E
        self.instructions[0xCB][0xEC] = lambda: self.SET(self.H, 5) # 0xCBEC = SET 5, H
        self.instructions[0xCB][0xED] = lambda: self.SET(self.L, 5) # 0xCBED = SET 5, L
        self.instructions[0xCB][0xEF] = lambda: self.SET(self.A, 5) # 0xCBEF = SET 5, A
        self.instructions[0xCB][0xF0] = lambda: self.SET(self.B, 6) # 0xCBF0 = SET 6, B
        self.instructions[0xCB][0xF1] = lambda: self.SET(self.C, 6) # 0xCBF1 = SET 6, C
        self.instructions[0xCB][0xF2] = lambda: self.SET(self.D, 6) # 0xCBF2 = SET 6, D
        self.instructions[0xCB][0xF3] = lambda: self.SET(self.E, 6) # 0xCBF3 = SET 6, E
        self.instructions[0xCB][0xF4] = lambda: self.SET(self.H, 6) # 0xCBF4 = SET 6, H
        self.instructions[0xCB][0xF5] = lambda: self.SET(self.L, 6) # 0xCBF5 = SET 6, L
        self.instructions[0xCB][0xF7] = lambda: self.SET(self.A, 6) # 0xCBF7 = SET 6, A
        self.instructions[0xCB][0xF8] = lambda: self.SET(self.B, 7) # 0xCBF8 = SET 7, B
        self.instructions[0xCB][0xF9] = lambda: self.SET(self.C, 7) # 0xCBF9 = SET 7, C
        self.instructions[0xCB][0xFA] = lambda: self.SET(self.D, 7) # 0xCBFA = SET 7, D
        self.instructions[0xCB][0xFB] = lambda: self.SET(self.E, 7) # 0xCBFB = SET 7, E
        self.instructions[0xCB][0xFC] = lambda: self.SET(self.H, 7) # 0xCBFC = SET 7, H
        self.instructions[0xCB][0xFD] = lambda: self.SET(self.L, 7) # 0xCBFD = SET 7, L
        self.instructions[0xCB][0xFF] = lambda: self.SET(self.A, 7) # 0xCBFF = SET 7, A

        # define SET ?, (??) instructions
        self.instructions[0xCB][0xC6] = lambda: self.SET_addr(self.HL, 0) # 0xCBC6 = SET 0, (HL)
        self.instructions[0xCB][0xCE] = lambda: self.SET_addr(self.HL, 1) # 0xCBCE = SET 1, (HL)
        self.instructions[0xCB][0xD6] = lambda: self.SET_addr(self.HL, 2) # 0xCBD6 = SET 2, (HL)
        self.instructions[0xCB][0xDE] = lambda: self.SET_addr(self.HL, 3) # 0xCBDE = SET 3, (HL)
        self.instructions[0xCB][0xE6] = lambda: self.SET_addr(self.HL, 4) # 0xCBE6 = SET 4, (HL)
        self.instructions[0xCB][0xEE] = lambda: self.SET_addr(self.HL, 5) # 0xCBEE = SET 5, (HL)
        self.instructions[0xCB][0xF6] = lambda: self.SET_addr(self.HL, 6) # 0xCBF6 = SET 6, (HL)
        self.instructions[0xCB][0xFE] = lambda: self.SET_addr(self.HL, 7) # 0xCBFE = SET 7, (HL)

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

    # read 8 bits (1 byte) after the given register
    def read_8(self, register, delta=1):
        return self.memory[int(register.get()) + delta]

    # read 16 bits (2 bytes) after the given register
    def read_16(self, register, delta=1):
        orig = int(register.get())
        return uint16(self.memory[orig + delta] | (int(self.memory[orig + delta + 1]) << 8))

    # 0xF3
    def DI(self):
        self.ime = False
        self.enable_ime_after_next_instruction = False
        self.just_executed_ei = False
        return 1, 1

    # 0xFB
    def EI(self):
        self.enable_ime_after_next_instruction = True
        self.just_executed_ei = True
        return 1, 1

    # pop value from stack to register
    def POP(self, register):
        sp_orig = int(self.SP.get())
        register.set(int(self.memory[sp_orig]) | (int(self.memory[sp_orig + 1]) << 8))
        self.SP.set(sp_orig + 2)
        return 1, 3

    # push value from register onto stack: 0xC5, 0xD5, 0xE5, 0xF5
    def PUSH(self, value):
        sp_orig = int(self.SP.get())
        self.memory[sp_orig - 1] = uint8((value >> 8) & 0xFF)
        self.memory[sp_orig - 2] = uint8(value & 0xFF)
        self.SP.set(sp_orig - 2)
        return 1, 4

    # 0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF
    def RST(self, address):
        self.PUSH(int(self.PC.get()) + 1)
        self.PC.set(address)
        return 0, 4 # # moves PC, so return 0 bytes (to not move PC again in emulation loop)

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
        if not self.ime:
            return 0
        for mask, vector in INTERRUPT_VECTORS:
            if pending & mask:
                self.ime = False
                self.memory[0xFF0F] = interrupt_flags & (~mask & 0xFF)
                self.PUSH(int(self.PC.get()))
                self.PC.set(vector)
                return 5  # interrupt servicing costs 5 M-cycles
        return 0

    # 0x00
    def NOP(self):
        return 1, 1

    # 0x10
    def STOP(self):
        #self.is_stopped = True
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
        pending = int(self.memory[0xFFFF]) & int(self.memory[0xFF0F]) & 0x1F
        if (not self.ime) and pending:
            self.halt_bug = True
        else:
            self.is_halted = True
        return 1, 1

    # 0x40-0x45, 0x47-0x4D, 0x4F-0x55, 0x57-0x5D, 0x5F-0x65, 0x67-0x6D, 0x6F, 0x78-0x7D, 0x7F
    def LD_X_X(self, register_store, register_other):
        register_store.set(register_other.get())
        return 1, 1

    # 0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E
    def LD_X_d8(self, register):
        register.set(self.read_8(self.PC))
        return 2, 2

    # 0x36
    def LD_addr_d8(self, register_target_address):
        self.memory[int(register_target_address.get())] = int(self.read_8(self.PC))
        return 2, 3

    # 0x01, 0x11, 0x21, 0x31
    def LD_XX_d16(self, register):
        register.set(self.read_16(self.PC))
        return 3, 3

    # 0xF8
    def LD_XX_XX_s8(self, register_store, register_other):
        ro_orig = int(register_other.get())
        delta = int(int8(self.read_8(self.PC)))
        self.reset_flag_Z()
        self.reset_flag_N()
        if (ro_orig & 0x0F) + (delta & 0x0F) > 0x0F:
            self.set_flag_H()
        else:
            self.reset_flag_H()
        if (ro_orig & 0xFF) + (delta & 0xFF) > 0xFF:
            self.set_flag_C()
        else:
            self.reset_flag_C()
        register_store.set(ro_orig + delta)
        return 2, 3

    # 0xE0
    def LD_a8_X(self, register):
        address = 0xFF00 + int(self.read_8(self.PC))
        self.memory[address] = int(register.get())
        return 2, 3

    # 0xEA
    def LD_a16_X(self, register):
        self.memory[int(self.read_16(self.PC))] = int(register.get())
        return 3, 4

    # 0xF0
    def LD_X_a8(self, register):
        address = 0xFF00 + int(self.read_8(self.PC))
        register.set(int(self.memory[address]))
        return 2, 3

    # 0xFA
    def LD_X_a16(self, register):
        register.set(int(self.memory[int(self.read_16(self.PC))]))
        return 3, 4

    # 0x02, 0x12, 0x22, 0x32
    def LD_addr_X(self, register_source, register_target_address, register_target_delta=0):
        self.memory[int(register_target_address.get())] = int(register_source.get())
        if register_target_delta != 0:
            register_target_address.add(register_target_delta)
        return 1, 2

    # 0xE2
    def LD_addr_X_FF00(self, register_source, register_target_address):
        address = 0xFF00 + int(register_target_address.get())
        self.memory[address] = int(register_source.get())
        return 1, 2

    # 0x0A, 0x1A, 0x2A, 0x3A
    def LD_X_addr(self, register_source_address, register_target, register_source_delta=0):
        register_target.set(int(self.memory[int(register_source_address.get())]))
        if register_source_delta != 0:
            register_source_address.add(register_source_delta)
        return 1, 2

    # 0xF2
    def LD_X_addr_FF00(self, register_source_address, register_target):
        address = 0xFF00 + int(register_source_address.get())
        register_target.set(int(self.memory[address]))
        return 1, 2

    # 0x03, 0x13, 0x23, 0x33
    def INC_XX(self, register):
        register.add(1)
        return 1, 2

    # 0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x3C
    def INC_X(self, register):
        result = (int(register.get()) + 1) & 0xFF
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
        address = int(register_address.get())
        result = (int(self.memory[address]) + 1) & 0xFF
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
        result = (int(register.get()) + 255) & 0xFF # (X + 255) & 0xFF == (X - 1) & 0xFF
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
        address = int(register_address.get())
        result = (int(self.memory[address]) + 255) & 0xFF # (X + 255) & 0xFF == (X - 1) & 0xFF
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
        rs_orig = int(register_store.get())
        ro_orig = int(register_other.get())
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
        rs_orig = int(register_store.get())
        ro_orig = int(register_other.get())
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
        rs_orig = int(register_store.get())
        ro_orig = int(self.read_8(self.PC))
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
        ro_orig = int(self.read_8(self.PC))
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

    # 0x2F
    def CPL(self, register):
        register.set(~register.get())
        self.set_flag_N()
        self.set_flag_H()
        return 1, 1

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
        result = register_store.get() & self.read_8(self.PC)
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
        result = register_store.get() ^ self.read_8(self.PC)
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
        result = register_store.get() | self.read_8(self.PC)
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
            self.PC.add(2 + int(int8(self.read_8(self.PC))))
            return 0, 3 # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 2, 2

    # 0xC2, 0xC3, 0xCA, 0xD2, 0xDA
    def JP_a16(self, condition):
        if condition:
            self.PC.set(self.read_16(self.PC))
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
            self.PUSH(int(self.PC.get()) + 3)
            self.PC.set(self.read_16(self.PC))
            return 0, 6 # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 3, 3

    # 0xC0, 0xC8, 0xC9, 0xD0, 0xD8, 0xD9
    def RET(self, condition, num_cycles=5, ime=False):
        if condition:
            self.PC.set(self.read_16(self.SP, delta=0))
            self.SP.add(2)
            if ime:
                self.ime = True
            return 0, num_cycles # # moves PC, so return 0 bytes (to not move PC again in emulation loop)
        else:
            return 1, 2

    # 0xCB30, 0xCB31, 0xCB32, 0xCB33, 0xCB34, 0xCB35, 0xCB37
    def SWAP_X(self, register):
        orig = int(register.get())
        result = (orig << 4) | (orig >> 4)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        register.set(result)
        return 2, 2

    # 0xCB36
    def SWAP_addr(self, register_address):
        address = int(register_address.get())
        orig = int(self.memory[address])
        result = (orig << 4) | (orig >> 4)
        if result == 0:
            self.set_flag_Z()
        else:
            self.reset_flag_Z()
        self.reset_flag_N()
        self.reset_flag_H()
        self.reset_flag_C()
        self.memory[address] = result
        return 2, 4

    # 0xCB40-0xCB45, 0xCB47-0xCB4D, 0xCB4F-0xCB55, 0xCB57-0xCB5D, 0xCB5F-0xCB65, 0xCB67-0xCB6D, 0xCB6F-0xCB75, 0xCB77-0xCB7D, 0xCB7F
    def BIT(self, register, bit_num):
        if register.get_bit(bit_num):
            self.reset_flag_Z()
        else:
            self.set_flag_Z()
        self.reset_flag_N()
        self.set_flag_H()
        return 2, 2

    # 0xCB46, 0xCB4E, 0xCB56, 0xCB5E, 0xCB66, 0xCB6E, 0xCB76, 0xCB7E
    def BIT_addr(self, register_address, bit_num):
        if get_bit(int(self.memory[int(register_address.get())]), bit_num):
            self.reset_flag_Z()
        else:
            self.set_flag_Z()
        self.reset_flag_N()
        self.set_flag_H()
        return 2, 3

    # 0xCB80-0xCB85, 0xCB87-0xCB8D, 0xCB8F-0xCB95, 0xCB97-0xCB9D, 0xCB9F-0xCBA5, 0xCBA7-0xCBAD, 0xCBAF-0xCBB5, 0xCBB7-0xCBBD, 0xCBBF
    def RES(self, register, bit_num):
        register.reset_bit(bit_num)
        return 2, 2

    # 0xCB86, 0xCB8E, 0xCB96, 0xCB9E, 0xCBA6, 0xCBAE, 0xCBB6, 0xCBBE
    def RES_addr(self, register_address, bit_num):
        address = int(register_address.get())
        self.memory[address] = reset_bit(self.memory[address], bit_num)
        return 2, 4

    # 0xCBC0-0xCBC5, 0xCBC7-0xCBCD, 0xCBCF-0xCBD5, 0xCBD7-0xCBDD, 0xCBDF-0xCBE5, 0xCBE7-0xCBED, 0xCBEF-0xCBF5, 0xCBF7-0xCBFD, 0xCBFF
    def SET(self, register, bit_num):
        register.set_bit(bit_num)
        return 2, 2

    # 0xCBC6, 0xCBCE, 0xCBD6, 0xCBDE, 0xCBE6, 0xCBEE, 0xCBF6, 0xCBFE
    def SET_addr(self, register_address, bit_num):
        address = int(register_address.get())
        self.memory[address] = set_bit(self.memory[address], bit_num)
        return 2, 4

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
                '''
                if event.type == pygame.KEYDOWN:
                    self.is_stopped = False
                '''
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

                # handle CPU stop
                '''
                if self.is_stopped:
                    num_m_cycles = 1
                    self.ppu.step(num_m_cycles)
                    m_cycles_remaining -= num_m_cycles
                    continue
                '''

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
                if self.halt_bug:
                    self.halt_bug = False
                else:
                    self.PC.add(num_bytes)
                self.ppu.step(num_m_cycles)
                m_cycles_remaining -= num_m_cycles

                # handle IME
                if self.enable_ime_after_next_instruction:
                    if self.just_executed_ei:
                        self.just_executed_ei = False
                    else:
                        self.ime = True
                        self.enable_ime_after_next_instruction = False

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
