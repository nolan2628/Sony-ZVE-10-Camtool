/* jit_boot_hook.s - 探针：验证「Jiritsu 是否在开机时自动 dlopen libJit.so」
 *
 * 行为（只有三个 syscall，绝不 fork / exec / 改 fd）：
 *   open("/tmp/jiritsu/BOOT_HOOK.bin", O_WRONLY|O_CREAT|O_APPEND, 0644)
 *   write(fd, "X", 1)
 *   close(fd)
 *
 * 设计要点：用 O_APPEND 写 1 字节，所以**每次加载都会让文件长 1 字节**。
 *   部署后立刻执行一次 → 1 字节
 *   重启后若变成 2 字节 → 证明开机时自动加载了
 *   仍是 1 字节      → 证明开机不加载
 *
 * 安全性：不写固件、不动其它文件、不改变进程状态；最坏情况是文件多一字节。
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
    movw r1, #0x441             /* O_WRONLY|O_CREAT|O_APPEND */
    movw r2, #0x1a4             /* 0644 */
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  done                   /* open 失败就安静返回 */

    movw r7, #4                 /* write */
    mov  r0, r4
    adr  r1, msg
    movw r2, #1
    svc  #0

    movw r7, #6                 /* close */
    mov  r0, r4
    svc  #0

done:
    pop  {r4, pc}

p_out:
    .ascii "/tmp/jiritsu/BOOT_HOOK.bin\0"
    .balign 4
msg:
    .ascii "X"
    .balign 4
