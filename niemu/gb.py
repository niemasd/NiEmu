#! /usr/bin/env python3
'''
Nintendo Game Boy Emulator

https://gbdev.io/pandocs
https://meganesu.github.io/generate-gb-opcodes
https://rgbds.gbdev.io/docs/v1.0.1/gbz80.7
'''

# imports
from niemu.common import load_game_data, Memory, Register8, Register8Pair, Register16
from numpy import uint16
import pygame

# constants
WIDTH = 160
HEIGHT = 144
FPS = 59.73
BOOTROM = [0x31,0xFE,0xFF,0xAF,0x21,0xFF,0x9F,0x32,0xCB,0x7C,0x20,0xFB,0x21,0x26,0xFF,0x0E,0x11,0x3E,0x80,0x32,0xE2,0x0C,0x3E,0xF3,0xE2,0x32,0x3E,0x77,0x77,0x3E,0xFC,0xE0,0x47,0x11,0x04,0x01,0x21,0x10,0x80,0x1A,0xCD,0x95,0x00,0xCD,0x96,0x00,0x13,0x7B,0xFE,0x34,0x20,0xF3,0x11,0xD8,0x00,0x06,0x08,0x1A,0x13,0x22,0x23,0x05,0x20,0xF9,0x3E,0x19,0xEA,0x10,0x99,0x21,0x2F,0x99,0x0E,0x0C,0x3D,0x28,0x08,0x32,0x0D,0x20,0xF9,0x2E,0x0F,0x18,0xF3,0x67,0x3E,0x64,0x57,0xE0,0x42,0x3E,0x91,0xE0,0x40,0x04,0x1E,0x02,0x0E,0x0C,0xF0,0x44,0xFE,0x90,0x20,0xFA,0x0D,0x20,0xF7,0x1D,0x20,0xF2,0x0E,0x13,0x24,0x7C,0x1E,0x83,0xFE,0x62,0x28,0x06,0x1E,0xC1,0xFE,0x64,0x20,0x06,0x7B,0xE2,0x0C,0x3E,0x87,0xE2,0xF0,0x42,0x90,0xE0,0x42,0x15,0x20,0xD2,0x05,0x20,0x4F,0x16,0x20,0x18,0xCB,0x4F,0x06,0x04,0xC5,0xCB,0x11,0x17,0xC1,0xCB,0x11,0x17,0x05,0x20,0xF5,0x22,0x23,0x22,0x23,0xC9,0xCE,0xED,0x66,0x66,0xCC,0x0D,0x00,0x0B,0x03,0x73,0x00,0x83,0x00,0x0C,0x00,0x0D,0x00,0x08,0x11,0x1F,0x88,0x89,0x00,0x0E,0xDC,0xCC,0x6E,0xE6,0xDD,0xDD,0xD9,0x99,0xBB,0xBB,0x67,0x63,0x6E,0x0E,0xEC,0xCC,0xDD,0xDC,0x99,0x9F,0xBB,0xB9,0x33,0x3E,0x3C,0x42,0xB9,0xA5,0xB9,0xA5,0x42,0x3C,0x21,0x04,0x01,0x11,0xA8,0x00,0x1A,0x13,0xBE,0x20,0xFE,0x23,0x7D,0xFE,0x34,0x20,0xF5,0x06,0x19,0x78,0x86,0x23,0x05,0x20,0xFB,0x86,0x20,0xFE,0x3E,0x01,0xE0,0x50]

# class to emulate Nintendo Game Boy
class GameBoy:
    # initialize a GameBoy object
    def __init__(self):
        # 8-bit registers
        self.A = Register8(0x01)
        self.F = Register8(0xB0)
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

        # memory and other key variables
        self.memory = Memory(0x10000)
        self.cartridge = None
        self.instructions = [None]*0x100
        self.instructions[0xCB] = [None]*0x100

        # define special instructions
        self.instructions[0x00] = self.NOP # 0x00 = NOP

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

    # 0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x3E
    def LD_X_d8(self, register):
        register.set(self.read_PC_8())
        return 2, 2

    # 0x36 = LD (HL), d8
    def LD_addr_d8(self, register_target_address):
        self.memory[register_target_address.get()] = self.read_PC_8()
        return 2, 3

    # 0x01, 0x11, 0x21, 0x31
    def LD_XX_d16(self, register):
        register.set(self.read_PC_16())
        return 3, 3

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
        pygame.display.set_caption(self.cartridge[0x0134 : 0x0144].rstrip(b'\x00').decode().strip())
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
                if opcode == 0xCB:
                    cb_opcode = self.memory[pc_orig + 1]
                    try:
                        num_bytes, num_cycles = self.instructions[0xCB][cb_opcode]()
                    except:
                        raise ValueError(f"Unknown opcode: 0xCB{cb_opcode:02X}")
                else:
                    try:
                        num_bytes, num_m_cycles = self.instructions[opcode]()
                    except:
                        raise ValueError(f"Unknown opcode: 0x{opcode:02X}")
                self.PC.add(num_bytes)
                m_cycles_remaining -= num_m_cycles

            # update video
            pass # TODO
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
