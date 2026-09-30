/* Small coordination helper: hold Java-compatible POSIX save lock until EOF. */
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/stat.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int fd = open(argv[1], O_RDWR | O_NOFOLLOW);
    if (fd < 0) { perror("session.lock"); return 2; }
    struct stat info;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode)) { close(fd); return 2; }
    struct flock fence = {0};
    fence.l_type = F_WRLCK; fence.l_whence = SEEK_SET;
    if (fcntl(fd, F_SETLK, &fence)) { perror("world open"); close(fd); return 3; }
    puts("locked"); fflush(stdout);
    while (getchar() != EOF) {}
    close(fd);
    return 0;
}
