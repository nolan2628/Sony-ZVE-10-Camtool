    .syntax unified
    .thumb
    .text
    .global _init
    .type _init, %function

@ jit_exec_v11 - 在 im.elf 内 fork 出子进程执行 /tmp/jiritsu/run.sh
@
@ v11 变更：子进程在 execve 前 **关闭 3..255 所有继承的 fd**。
@   原因：im.elf 持有 USB gadget / 视频节点的句柄；常驻子进程若继承它们，
@   服务退出、USB 重枚举时设备会整个掉线（实测两次 spawn 后相机失联）。
@
@ 其余同 v10：绝不在 ctor 里直接 execve（会杀死 188 线程的 im.elf），
@ 输出统一重定向到 /tmp/jiritsu/out.txt。
@
@ ARM EABI: fork=2 execve=11 open=5 close=6 dup2=63 exit=1
_init:
    push {r4, r5, lr}

    @ ---- fork ----
    movs r7, #2
    svc  #0
    cmp  r0, #0
    blt  done              @ fork 失败，什么都不做
    bne  done              @ 父进程（im.elf）：立刻返回，影像不受影响

    @ ---- 子进程 ----
    @ 关闭所有继承 fd（3..255），把 im.elf 的 USB/视频句柄还回去
    movs r5, #3
1:
    movs r7, #6
    mov  r0, r5
    svc  #0
    adds r5, r5, #1
    cmp  r5, #0x100
    blo  1b

    @ fd = open("/tmp/jiritsu/out.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644)
    movs r7, #5
    adr  r0, p_out
    movw r1, #0x241
    movw r2, #0x1a4
    svc  #0
    mov  r4, r0
    cmp  r4, #0
    blt  no_dup

    @ dup2(fd, 1)  stdout
    movs r7, #63
    mov  r0, r4
    movs r1, #1
    svc  #0

    @ dup2(fd, 2)  stderr
    movs r7, #63
    mov  r0, r4
    movs r1, #2
    svc  #0

    @ close(fd)
    movs r7, #6
    mov  r0, r4
    svc  #0

no_dup:
    @ 在栈上构造 argv / envp（避免重定位，纯 PC 相对）
    sub  sp, #32
    adr  r0, p_sh
    str  r0, [sp]          @ argv[0] = "/bin/sh"
    adr  r0, p_run
    str  r0, [sp, #4]      @ argv[1] = "/tmp/jiritsu/run.sh"
    movs r0, #0
    str  r0, [sp, #8]      @ argv[2] = NULL
    adr  r0, p_env
    str  r0, [sp, #16]     @ envp[0] = "PATH=..."
    movs r0, #0
    str  r0, [sp, #20]     @ envp[1] = NULL

    @ execve("/bin/sh", argv, envp)
    movs r7, #11
    adr  r0, p_sh
    mov  r1, sp
    add  r2, sp, #16
    svc  #0

    @ execve 失败 -> exit(127)
    movs r7, #1
    movs r0, #127
    svc  #0

done:
    pop  {r4, r5, pc}

    .balign 4
p_sh:
    .ascii "/bin/sh\0"
    .balign 4
p_run:
    .ascii "/tmp/jiritsu/run.sh\0"
    .balign 4
p_out:
    .ascii "/tmp/jiritsu/out.txt\0"
    .balign 4
p_env:
    .ascii "PATH=/bin:/sbin:/usr/bin:/usr/sbin\0"
    .balign 4
