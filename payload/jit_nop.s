    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function

@ 空构造函数：只为验证「dlopen 本身会不会崩」
_init:
    bx lr
