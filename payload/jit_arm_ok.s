/* jit_arm_ok.s - ARM 模式（非 Thumb）最小载荷，用来验证 DT_INIT|0 是否正确
 * 行为: open("/tmp/jiritsu/ARM_OK.txt") -> write("ARM") -> close -> 返回
 * 目的: 区分「ARM 模式载荷本身不能用」还是「某个具体载荷的代码有问题」
 */
    .syntax unified
    .arm
    .text
    .global _init
    .type _init, %function
_init:
    push {r4, lr}

    movw r7, #5                 /* open */
    adr  r0, p_out
    movw r1, #0x241             /* O_WRONLY|O_CREAT|O_TRUNC */
    movw r2, #0x1a4             /* 0644 */
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done

    movw r7, #4                 /* write */
    mov  r0, r4
    adr  r1, msg
    movw r2, #3
    svc  #0

    movw r7, #6                 /* close */
    mov  r0, r4
    svc  #0

done:
    pop  {r4, pc}

p_out:
    .ascii "/tmp/jiritsu/ARM_OK.txt\0"
    .balign 4
msg:
    .ascii "ARM"
    .balign 4
