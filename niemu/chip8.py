#! /usr/bin/env python3
'''
CHIP-8 Emulator

https://austinmorlan.com/posts/chip8_emulator
https://multigesture.net/articles/how-to-write-an-emulator-chip-8-interpreter
'''

# imports
from niemu.common import load_game_data, Memory, Register8, Register16
from random import randint

# constants
FONT_SET = [
    0xF0, 0x90, 0x90, 0x90, 0xF0, # 0
    0x20, 0x60, 0x20, 0x20, 0x70, # 1
    0xF0, 0x10, 0xF0, 0x80, 0xF0, # 2
    0xF0, 0x10, 0xF0, 0x10, 0xF0, # 3
    0x90, 0x90, 0xF0, 0x10, 0x10, # 4
    0xF0, 0x80, 0xF0, 0x10, 0xF0, # 5
    0xF0, 0x80, 0xF0, 0x90, 0xF0, # 6
    0xF0, 0x10, 0x20, 0x40, 0x40, # 7
    0xF0, 0x90, 0xF0, 0x90, 0xF0, # 8
    0xF0, 0x90, 0xF0, 0x10, 0xF0, # 9
    0xF0, 0x90, 0xF0, 0x90, 0x90, # A
    0xE0, 0x90, 0xE0, 0x90, 0xE0, # B
    0xF0, 0x80, 0x80, 0x80, 0xF0, # C
    0xE0, 0x90, 0x90, 0x90, 0xE0, # D
    0xF0, 0x80, 0xF0, 0x80, 0xF0, # E
    0xF0, 0x80, 0xF0, 0x80, 0x80, # F
]

# class to emulate CHIP-8
class CHIP8:
    # initialize a CHIP8 object
    def __init__(self):
        # initialize member variables
        self.V  = [Register8(0) for _ in range(16)]     # 8-bit registers
        self.I  = Register16(0)                         # Index Register (I)
        self.PC = Register16(0x200)                     # Program Counter (PC)
        self.DT = Register8(0)                          # Delay Timer (DT)
        self.ST = Register8(0)                          # Sound Timer (ST)
        self.SP = Register8(0)                          # Stack Pointer
        self.stack = [Register16(0) for _ in range(16)] # Stack
        self.graphics = [[False]*64]*32                 # Monochrome Graphics (64 x 32)
        self.key = [False]*16                           # State of Input Keys (True = Pressed)
        self.draw_flag = False                          # Draw Flag
        self.memory = Memory(0x1000)                    # Memory (4 KB)
        self.memory[:len(FONT_SET)] = FONT_SET          # Load font set into memory

        # define instructions
        self.instructions = [None]*0x10000
        self.instructions[0x00E0] = self.CLS
        self.instructions[0x00EE] = self.RET
        for nnn in range(0x000, 0x1000):
            self.instructions[0x1000 | nnn] = lambda: self.JP(nnn)
            self.instructions[0x2000 | nnn] = lambda: self.CALL(nnn)
        for x in range(16):
            upper = 0x3000 | (x << 2)
            for kk in range(0x00, 0x100):
                self.instructions[upper | kk] = lambda: self.SE(self.V[x], kk)

    # 0x00E0 = CLS = Clear Screen
    def CLS(self):
        self.graphics = [[False]*64]*32

    # 0x00EE = RET = Return from Subroutine
    def RET(self):
        self.SP.add(-1)
        self.PC.set(self.stack[self.SP.get()].get())

    # 0x1NNN = JP = Jump to Address NNN
    def JP(self, nnn):
        self.PC.set(nnn)

    # 0x2NNN = CALL = Call Subroutine at Address NNN
    def CALL(self, nnn):
        self.stack[self.SP.get()].set(self.PC.get())
        self.SP.add(1)
        self.PC.set(nnn)

    # 0x3XKK = SE VX, KK = Skip Next Instruction if VX == KK
    def SE(self, vx, kk):
        if vx.get() == kk:
            self.PC.add(2)

    # load a game
    def load_game(self, path):
        data = load_game_data(path)
        self.memory[0x200 : 0x200 + len(data)] = memoryview(data)

    # emulation loop
    def run(self):
        while True:
            self.emulate_cycle()
            if self.draw_flag:
                self.draw_graphics()
            self.set_keys()

    # emulate a single cycle
    def emulate_cycle(self):
        pc_orig = self.PC.get()
        opcode = (self.memory[pc_orig] << 8) | self.memory[pc_orig + 1]
        try:
            instruction = self.instructions[opcode]
            assert instruction is not None
        except:
            raise ValueError(f"Unknown opcode: 0x{opcode:02X}")

# run program
if __name__ == "__main__":
    from sys import argv
    assert len(argv) == 2, "USAGE: %s <game_rom>" % argv[0]
    chip8 = CHIP8()
    chip8.load_game(argv[1])
    chip8.run()
