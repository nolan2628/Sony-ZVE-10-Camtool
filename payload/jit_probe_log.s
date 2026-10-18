    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function

@ jit_probe_log - 最小 ctor：往 /log/ctor_proof.txt 写一行，用来判断
@ 「构造函数到底有没有执行」。/log 是常驻挂载的 500MB vfat，不依赖 /tmp/jiritsu。
@ open=5 write=4 close=6
_init:
    push {r4, lr}

    movs r7, #5
    adr  r0, p_out
    movw r1, #0x241          @ O_WRONLY|O_CREAT|O_TRUNC
    movw r2, #0x1a4          @ 0644
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done

    movs r7, #4
    mov  r0, r4
    adr  r1, msg
    movs r2, #10
    svc  #0

    movs r7, #6
    mov  r0, r4
    svc  #0

done:
    pop  {r4, pc}

    .balign 4
p_out:
    .ascii "/log/ctor_proof.txt\0"
    .balign 4
msg:
    .ascii "CTOR_RAN\n\0"
    .balign 4
