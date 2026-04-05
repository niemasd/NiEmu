#! /usr/bin/env python3
'''
Common variables, classes, functions, etc.
'''

# imports
from numpy import column_stack, int16, linspace, sin, uint8, uint16, zeros
from numpy import pi as PI
from pathlib import Path
import pygame

# constants
ARCHIVE_EXTS = {'.zip'}
COLOR_BLACK = (  0,   0,   0)
COLOR_WHITE = (255, 255, 255)

# dummy function that doesn't do anything
def dummy():
    pass

# open a file for reading/writing
def open_file(path, mode='rb'):
    if isinstance(path, str):
        path = Path(path)
    if path.suffix.strip().lower() == '.gz':
        return gopen(path, mode=mode)
    else:
        return open(path, mode=mode)

# load game data of a specific extension
def load_game_data(path, ext=None):
    if isinstance(path, str):
        path = Path(path)
    path_ext = path.suffix.strip().lower()
    if path_ext in ARCHIVE_EXTS:
        if (ext is None) or (len(ext) == 0):
            raise ValueError("Must specify game file extension if loading game data from an archive")
        if not ext.startswith('.'):
            ext = '.' + ext
        if path_ext == '.zip':
            with ZipFile(path, 'r') as z:
                for entry in z.infolist():
                    if entry.filename.strip().lower().endswith(ext):
                        return z.read(entry.filename)
        else:
            raise NotImplementedError("%s not implemented" % path_ext)
    else:
        with open_file(path, mode='rb') as f:
            return f.read()

# return the n-th bit of a bool (0 = False, 1 = True)
def get_bit(bit_num, value):
    return bool((value >> bit_num) & 0b1)

# return the result of resetting the n-th bit of an integer to 0
def reset_bit(bit_num, value):
    return uint8(value & ~(0b1 << bit_num))

# return the result of setting the n-th bit of an integer to 1
def set_bit(bit_num, value):
    return uint8(value | (0b1 << bit_num))

# class to represent memory
class Memory:
    def __init__(self, size):
        self.data = zeros(size, dtype=uint8)
    def __getitem__(self, i):
        return self.data[i]
    def __setitem__(self, i, x):
        self.data[i] = x
    def __len__(self):
        return len(self.data)
    def __str__(self):
        return '\n'.join(' '.join(f'0x{self[i]:02X}' for i in range(row_start, row_start + 0x10)) for row_start in range(0, len(self), 0x10))

# class to represent a register
class Register:
    def __init__(self, value=0):
        self.set(value)
    def __str__(self):
        return '0x%s' % hex(self.get())
    def set(self, value):
        self.data = value
    def get(self):
        return self.data
    def add(self, value): # negate value to subtract
        self.set(self.get() + value)

# class to represent an 8-bit register
class Register8(Register):
    def __str__(self):
        return '0x%s' % hex(self.get()).zfill(2)
    def set(self, value):
        self.data = uint8(value)

# class to represent a 16-bit register
class Register16(Register):
    def __str__(self):
        return '0x%s' % hex(self.get()).zfill(4)
    def set(self, value):
        self.data = uint16(value)

# generate a sine wave tone
def generate_tone_sine(frequency=440, duration=1.0, sample_rate=44100):
    t = linspace(0, duration, int(sample_rate * duration), False)
    wave = 0.5 * sin(2 * PI * frequency * t)
    audio = (wave * 0x7FFF).astype(int16)
    stereo_audio = column_stack((audio, audio))
    return pygame.sndarray.make_sound(stereo_audio)
