    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function
_init:
    push {r4, r5, r6, r7, lr}
    sub  sp, #0x1000
    mov  r6, sp

    @ --- read /proc/mounts -> r6 ---
    adr  r0, p_mounts
    movs r1, #0
    movs r2, #0
    movs r7, #5
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done
    mov  r0, r4
    mov  r1, r6
    movw r2, #0x800
    movs r7, #3
    svc  #0
    mov  r5, r0
    mov  r0, r4
    movs r7, #6
    svc  #0
    cmp  r5, #0
    ble  done

    @ --- read /proc/partitions -> r6+0x800 ---
    adr  r0, p_parts
    movs r1, #0
    movs r2, #0
    movs r7, #5
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  write_out
    mov  r0, r4
    add  r1, r6, #0x800
    movw r2, #0x800
    movs r7, #3
    svc  #0
    cmp  r0, #0
    ble  close_in
    add  r5, r5, r0
close_in:
    mov  r0, r4
    movs r7, #6
    svc  #0

write_out:
    adr  r0, p_out
    movw r1, #0x241
    movw r2, #0x1a4
    movs r7, #5
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done
    mov  r0, r4
    mov  r1, r6
    mov  r2, r5
    movs r7, #4
    svc  #0
    mov  r0, r4
    movs r7, #6
    svc  #0
done:
    add  sp, #0x1000
    pop  {r4, r5, r6, r7, pc}
    .balign 4
p_mounts:
    .ascii "/proc/mounts\0"
    .balign 4
p_parts:
    .ascii "/proc/partitions\0"
    .balign 4
p_out:
    .ascii "/tmp/jiritsu/zzz_sysinfo.txt\0"
    .balign 4
