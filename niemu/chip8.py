#! /usr/bin/env python3
'''
CHIP-8 Emulator

https://austinmorlan.com/posts/chip8_emulator
https://multigesture.net/articles/how-to-write-an-emulator-chip-8-interpreter
'''

# imports
from niemu.common import COLOR_BLACK, COLOR_WHITE, generate_tone_sine, load_game_data, Memory, Register8, Register16
from random import randint
import pygame

# constants
WIDTH  = 64
HEIGHT = 32
FPS = 60
CYCLES_PER_FRAME = 10
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
KEY_MAP = [
    pygame.K_x, # 0x0
    pygame.K_1, # 0x1
    pygame.K_2, # 0x2
    pygame.K_3, # 0x3
    pygame.K_q, # 0x4
    pygame.K_w, # 0x5
    pygame.K_e, # 0x6
    pygame.K_a, # 0x7
    pygame.K_s, # 0x8
    pygame.K_d, # 0x9
    pygame.K_z, # 0xA
    pygame.K_c, # 0xB
    pygame.K_4, # 0xC
    pygame.K_r, # 0xD
    pygame.K_f, # 0xE
    pygame.K_v, # 0xF
]

# class to emulate CHIP-8
class CHIP8:
    # initialize a CHIP8 object
    def __init__(self):
        # initialize member variables
        self.V  = [Register8(0) for _ in range(16)]         # 8-bit registers
        self.I  = Register16(0)                             # Index Register (I)
        self.PC = Register16(0x200)                         # Program Counter (PC)
        self.DT = Register8(0)                              # Delay Timer (DT)
        self.ST = Register8(0)                              # Sound Timer (ST)
        self.SP = Register8(0)                              # Stack Pointer
        self.stack = [Register16(0) for _ in range(16)]     # Stack
        self.video = [[False]*WIDTH for _ in range(HEIGHT)] # Monochrome Video (64 x 32)
        self.keypad = [False]*16                            # State of Input Keys (True = Pressed)
        self.memory = Memory(0x1000)                        # Memory (4 KB)
        self.memory[0x50 : 0x50 + len(FONT_SET)] = FONT_SET # Load font set into memory

        # define instructions
        self.instructions = [None]*0x10000
        self.instructions[0x00E0] = self.CLS
        self.instructions[0x00EE] = self.RET
        for nnn in range(0x000, 0x1000):
            self.instructions[0x1000 | nnn] = lambda nnn=nnn: self.JP  (nnn)
            self.instructions[0x2000 | nnn] = lambda nnn=nnn: self.CALL(nnn)
            self.instructions[0xA000 | nnn] = lambda nnn=nnn: self.LD  (self.I, nnn)
            self.instructions[0xB000 | nnn] = lambda nnn=nnn: self.JP  (nnn + self.V[0].get())
        for x in range(16):
            vx = self.V[x]
            x00 = x << 8
            mask_se_vx_kk    = 0x3000 | x00
            mask_sne_vx_kk   = 0x4000 | x00
            mask_se_vx_vy    = 0x5000 | x00
            mask_ld_vx_kk    = 0x6000 | x00
            mask_add_vx_kk   = 0x7000 | x00
            mask_ld_vx_vy    = 0x8000 | x00
            mask_or_vx_vy    = 0x8001 | x00
            mask_and_vx_vy   = 0x8002 | x00
            mask_xor_vx_vy   = 0x8003 | x00
            mask_add_vx_vy   = 0x8004 | x00
            mask_sub_vx_vy   = 0x8005 | x00
            mask_shr_vx_vy   = 0x8006 | x00
            mask_subn_vx_vy  = 0x8007 | x00
            mask_shl_vx_vy   = 0x800E | x00
            mask_sne_vx_vy   = 0x9000 | x00
            mask_rnd_vx_kk   = 0xC000 | x00
            mask_drw_vx_vy_n = 0xD000 | x00
            self.instructions[0xE09E | x00] = lambda vx=vx: self.SKP   (vx)
            self.instructions[0xE0A1 | x00] = lambda vx=vx: self.SKNP  (vx)
            self.instructions[0xF007 | x00] = lambda vx=vx: self.LD    (vx, self.DT.get())
            self.instructions[0xF00A | x00] = lambda vx=vx: self.LD_KEY(vx)
            self.instructions[0xF015 | x00] = lambda vx=vx: self.LD    (self.DT, vx.get())
            self.instructions[0xF018 | x00] = lambda vx=vx: self.LD    (self.ST, vx.get())
            self.instructions[0xF01E | x00] = lambda vx=vx: self.I.add (vx.get())
            self.instructions[0xF029 | x00] = lambda vx=vx: self.LD    (self.I,  0x50 + (5 * vx.get()))
            self.instructions[0xF033 | x00] = lambda vx=vx: self.LD_B  (self.I,  vx.get())
            self.instructions[0xF055 | x00] = lambda x=x: self.LD_RANGE_I_VX(x)
            self.instructions[0xF065 | x00] = lambda x=x: self.LD_RANGE_VX_I(x)
            for kk in range(0x00, 0x100):
                self.instructions[mask_se_vx_kk  | kk] = lambda vx=vx, kk=kk: self.SE (vx, kk)
                self.instructions[mask_sne_vx_kk | kk] = lambda vx=vx, kk=kk: self.SNE(vx, kk)
                self.instructions[mask_ld_vx_kk  | kk] = lambda vx=vx, kk=kk: self.LD (vx, kk)
                self.instructions[mask_add_vx_kk | kk] = lambda vx=vx, kk=kk: vx.add  (kk)
                self.instructions[mask_rnd_vx_kk | kk] = lambda vx=vx, kk=kk: self.RND(vx, kk)
            for y in range(16):
                vy = self.V[y]
                y0 = y << 4
                mask_drw_vx_vy_n_y0 = mask_drw_vx_vy_n | y0
                self.instructions[mask_se_vx_vy   | y0] = lambda vx=vx, vy=vy: self.SE (vx, vy.get())
                self.instructions[mask_ld_vx_vy   | y0] = lambda vx=vx, vy=vy: self.LD (vx, vy.get())
                self.instructions[mask_or_vx_vy   | y0] = lambda vx=vx, vy=vy: self.OR (vx, vy.get())
                self.instructions[mask_and_vx_vy  | y0] = lambda vx=vx, vy=vy: self.AND(vx, vy.get())
                self.instructions[mask_xor_vx_vy  | y0] = lambda vx=vx, vy=vy: self.XOR(vx, vy.get())
                self.instructions[mask_add_vx_vy  | y0] = lambda vx=vx, vy=vy: self.ADD(vx, vy.get())
                self.instructions[mask_sub_vx_vy  | y0] = lambda vx=vx, vy=vy: self.SUB(vx, vy.get())
                self.instructions[mask_shr_vx_vy  | y0] = lambda vx=vx, vy=vy: self.SHR(vx)
                self.instructions[mask_subn_vx_vy | y0] = lambda vx=vx, vy=vy: self.SUBN(vx, vy.get())
                self.instructions[mask_shl_vx_vy  | y0] = lambda vx=vx, vy=vy: self.SHL(vx)
                self.instructions[mask_sne_vx_vy  | y0] = lambda vx=vx, vy=vy: self.SNE(vx, vy.get())
                for n in range(16):
                    self.instructions[mask_drw_vx_vy_n_y0 | n] = lambda vx=vx, vy=vy, n=n: self.DRW(vx.get(), vy.get(), n)

    # 0x00E0 = CLS = Clear Screen
    def CLS(self):
        self.video = [[False]*WIDTH for _ in range(HEIGHT)]

    # 0x00EE = RET = Return from Subroutine
    def RET(self):
        self.SP.add(-1)
        self.PC.set(self.stack[self.SP.get()].get())

    # 0x1NNN = JP NNN = Jump to Address NNN
    # 0xBNNN = JP V0, NNN = Jump to Address NNN + V0
    def JP(self, address):
        self.PC.set(address)

    # 0x2NNN = CALL NNN = Call Subroutine at Address NNN
    def CALL(self, address):
        self.stack[self.SP.get()].set(self.PC.get())
        self.SP.add(1)
        self.PC.set(address)

    # 0x3XKK = SE VX, KK = Skip Next Instruction if VX == KK
    # 0x5XY0 = SE VX, VY = Skip Next Instruction if VX == VY
    def SE(self, register, value):
        if register.get() == value:
            self.PC.add(2)

    # 0x4XKK = SNE VX, KK = Skip Next Instruction if VX != KK
    # 0x9XY0 = SNE VX, VY = Skip Next Instruction if VX != VY
    def SNE(self, register, value):
        if register.get() != value:
            self.PC.add(2)

    # 0x6XKK = LD VX, KK = Load KK into VX
    # 0x8XY0 = LD VX, VY = Load VY into VX
    # 0xANNN = LD I, NNN = Load NNN into I
    # 0xFX07 = LD VX, DT = Load DT into VX
    # 0xFX15 = LD DT, VX = Load VX into DT
    # 0xFX18 = LD ST, VX = Load VX into ST
    # 0xFX29 = LD F, VX = Load Font of Digit VX into I
    def LD(self, register, value):
        register.set(value)

    # 0xFX0A = LD VX, K = Wait for Key to be Pressed, and Load It into VX
    def LD_KEY(self, register):
        repeat = True
        for i in range(16):
            if self.keypad[i]:
                register.set(i)
                repeat = False
                break
        if repeat:
            self.PC.add(-2)

    # 0xFX33 = LD B, VX = Load BCD Representation of VX into I, I+1, and I+2
    def LD_B(self, register, value):
        orig_i = register.get()
        self.memory[orig_i + 2] = value % 10
        value //= 10
        self.memory[orig_i + 1] = value % 10
        value //= 10
        self.memory[orig_i    ] = value % 10

    # 0xFX55 = LD [I], VX = Load V0 through VX into I through I+X
    def LD_RANGE_I_VX(self, x):
        orig_i = self.I.get()
        for i in range(x + 1):
            self.memory[orig_i + i] = self.V[i].get()

    # 0xFX65 = LD VX, [I] = Load I through I+X into V0 through VX
    def LD_RANGE_VX_I(self, x):
        orig_i = self.I.get()
        for i in range(x + 1):
            self.V[i].set(self.memory[orig_i + i])

    # 0x8XY1 = OR VX, VY = Set VX to VX | VY
    def OR(self, register, value):
        register.set(register.get() | value)

    # 0x8XY2 = AND VX, VY = Set VX to VX & VY
    def AND(self, register, value):
        register.set(register.get() & value)

    # 0x8XY3 = XOR VX, VY = Set VX to VX ^ VY
    def XOR(self, register, value):
        register.set(register.get() ^ value)

    # 0x8XY4 = ADD VX, VY = Increase VX by VY
    def ADD(self, register, value):
        orig = register.get()
        result = (int(orig) + int(value)) & 0xFF
        self.V[0xF].set(result < orig)
        register.set(result)

    # 0x8XY5 = SUB VX, VY = Decrease VX by VY
    def SUB(self, register, value):
        orig = register.get()
        self.V[0xF].set(orig > value)
        result = orig - value
        while result < 0:
            result += 256
        register.set(result)

    # 0x8XY7 = SUBN VX, VY = Set VX to VY - VX
    def SUBN(self, register, value):
        orig = register.get()
        self.V[0xF].set(value > orig)
        result = value - orig
        while result < 0:
            result += 256
        register.set(result)

    # 0x8XY6 = SHR VX, VY = Shift VX Right (VY is ignored)
    def SHR(self, register):
        orig = register.get()
        self.V[0xF].set(orig & 0b1)
        register.set(orig >> 1)

    # 0x8XYE = SHL VX, VY = Shift VX Left (VY is ignored)
    def SHL(self, register):
        orig = register.get()
        self.V[0xF].set((orig >> 7) & 0b1)
        register.set(orig << 1)

    # 0xCXKK = RND VX, KK = Set VX to Random Byte & KK
    def RND(self, register, value):
        register.set(randint(0, 255) & value)

    # 0xDXYN = DRW VX, VY, N = Display N-byte Sprite Starting at (VX, VY)
    def DRW(self, x, y, height):
        x %= WIDTH
        y %= HEIGHT
        collision = False
        for row in range(height):
            sprite_byte = self.memory[self.I.get() + row]
            for col in range(8):
                if bool(sprite_byte & (0x80 >> col)):
                    px = (x + col) % WIDTH
                    py = (y + row) % HEIGHT
                    if self.video[py][px]:
                        collision = True
                    self.video[py][px] ^= True
        self.V[0xF].set(collision)

    # 0xEX9E = SKP VX = Skip Next Instruction if Key VX is Pressed
    def SKP(self, register):
        if self.keypad[register.get()]:
            self.PC.add(2)

    # 0xEXA1 = SKNP VX = Skip Next Instruction if Key VX is Not Pressed
    def SKNP(self, register):
        if not self.keypad[register.get()]:
            self.PC.add(2)

    # load a game
    def load_game(self, path):
        data = load_game_data(path, ext='.ch8')
        self.memory[0x200 : 0x200 + len(data)] = memoryview(data)

    # emulate a single cycle
    def cycle(self):
        pc_orig = self.PC.get()
        opcode = (self.memory[pc_orig] << 8) | self.memory[pc_orig + 1]
        self.PC.add(2)
        try:
            self.instructions[opcode]()
        except:
            raise ValueError(f"Unknown opcode: 0x{opcode:02X}")
        if self.DT.get() > 0:
            self.DT.add(-1)
        if self.ST.get() > 0:
            self.ST.add(-1)

    # emulation loop
    def run(self, cycles_per_frame=CYCLES_PER_FRAME):
        # set up pygame
        pygame.init()
        window = pygame.display.set_mode((WIDTH*15, HEIGHT*15))
        surface = pygame.Surface((WIDTH, HEIGHT))
        surface.fill(COLOR_BLACK)
        clock = pygame.time.Clock()

        # set up sound
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=1)
        sound = generate_tone_sine()
        channel = sound.play(loops=-1) # infinite loop
        channel.pause()
        sound_playing = False

        # run game
        running = True
        while running:
            # handle next key input
            for event in pygame.event.get():
                if (event.type == pygame.QUIT) or ((event.type == pygame.KEYDOWN) and (event.key == pygame.K_ESCAPE)):
                    running = False
                    break
            pressed = pygame.key.get_pressed()
            self.keypad = [pressed[KEY_MAP[i]] for i in range(16)]

            # update video
            for _ in range(cycles_per_frame):
                self.cycle()
            with pygame.PixelArray(surface) as pxarray:
                for y, row in enumerate(self.video):
                    for x, val in enumerate(row):
                        pxarray[x, y] = COLOR_WHITE if val else COLOR_BLACK
            pygame.transform.scale(surface, window.get_size(), window)
            pygame.display.flip()

            # update audio
            if self.ST.get() > 0:
                channel.unpause()
                sound_playing = True
            else:
                channel.pause()
                sound_playing = False

            # maintain FPS
            clock.tick(FPS)

# run program
if __name__ == "__main__":
    from sys import argv
    if len(argv) == 2:
        cycles_per_frame = CYCLES_PER_FRAME
    elif len(argv) == 3:
        cycles_per_frame = int(argv[2])
    else:
        raise ValueError("USAGE: %s <game_rom> [cycles_per_frame=%d]" % (argv[0], CYCLES_PER_FRAME))
    chip8 = CHIP8()
    chip8.load_game(argv[1])
    chip8.run(cycles_per_frame=cycles_per_frame)
