#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

struct network_probe {
    int phase;
    int error_number;
};

static struct network_probe probe_connect(int port) {
    struct network_probe result = {0, 0};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        result.phase = 1;
        result.error_number = errno;
        return result;
    }

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, "127.0.0.1", &address.sin_addr) != 1) {
        result.phase = 2;
        result.error_number = EINVAL;
        close(fd);
        return result;
    }

    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0) {
        result.phase = 3;
    } else {
        result.phase = 2;
        result.error_number = errno;
    }
    close(fd);
    return result;
}

static int is_known_role(const char *role) {
    return strcmp(role, "worker") == 0 || strcmp(role, "owned_server") == 0;
}

int main(int argc, char **argv) {
    if (argc != 7 || !is_known_role(argv[1])) {
        return 64;
    }

    const char *role = argv[1];
    const char *scratch = argv[2];
    const char *sentinel = argv[3];
    const int allowed_port = atoi(argv[4]);
    const int denied_port = atoi(argv[5]);
    const pid_t sentinel_pid = (pid_t)atoi(argv[6]);

    char allowed_path[4096];
    if (snprintf(allowed_path, sizeof(allowed_path), "%s/allowed.bin", scratch) >=
        (int)sizeof(allowed_path)) {
        return 64;
    }

    int allowed_write_errno = 0;
    int write_fd = open(allowed_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (write_fd < 0) {
        allowed_write_errno = errno;
    } else {
        if (write(write_fd, "probe-ok", 8) != 8) {
            allowed_write_errno = errno == 0 ? EIO : errno;
        }
        close(write_fd);
    }

    char readback[16] = {0};
    int allowed_read_errno = 0;
    int read_fd = open(allowed_path, O_RDONLY);
    if (read_fd < 0) {
        allowed_read_errno = errno;
    } else {
        if (read(read_fd, readback, 8) != 8) {
            allowed_read_errno = errno == 0 ? EIO : errno;
        }
        close(read_fd);
    }

    int protected_read_errno = 0;
    int protected_read_fd = open(sentinel, O_RDONLY);
    if (protected_read_fd < 0) {
        protected_read_errno = errno;
    } else {
        char byte;
        (void)read(protected_read_fd, &byte, 1);
        close(protected_read_fd);
    }

    int protected_write_errno = 0;
    int protected_write_fd = open(sentinel, O_WRONLY | O_TRUNC);
    if (protected_write_fd < 0) {
        protected_write_errno = errno;
    } else {
        (void)write(protected_write_fd, "bad", 3);
        close(protected_write_fd);
    }

    const struct network_probe allowed_network = probe_connect(allowed_port);
    const struct network_probe denied_network = probe_connect(denied_port);

    int sentinel_query_errno = 0;
    if (kill(sentinel_pid, 0) != 0) {
        sentinel_query_errno = errno;
    }
    int sentinel_signal_errno = 0;
    if (kill(sentinel_pid, SIGTERM) != 0) {
        sentinel_signal_errno = errno;
    }

    char *spawn_argv[] = {(char *)"/usr/bin/true", NULL};
    pid_t spawned_pid = 0;
    int spawn_error = posix_spawn(
        &spawned_pid, "/usr/bin/true", NULL, NULL, spawn_argv, environ);
    int spawned_status = -1;
    if (spawn_error == 0) {
        (void)waitpid(spawned_pid, &spawned_status, 0);
    }

    printf(
        "{\"role\":\"%s\",\"pid\":%d,\"uid\":%d,\"gid\":%d,"
        "\"allowed_write_errno\":%d,\"allowed_read_errno\":%d,"
        "\"allowed_readback\":\"%s\",\"protected_read_errno\":%d,"
        "\"protected_write_errno\":%d,\"allowed_network_phase\":%d,"
        "\"allowed_network_errno\":%d,\"denied_network_phase\":%d,"
        "\"denied_network_errno\":%d,\"sentinel_query_errno\":%d,"
        "\"sentinel_signal_errno\":%d,\"spawn_error\":%d,"
        "\"spawned_status\":%d}\n",
        role,
        (int)getpid(),
        (int)getuid(),
        (int)getgid(),
        allowed_write_errno,
        allowed_read_errno,
        readback,
        protected_read_errno,
        protected_write_errno,
        allowed_network.phase,
        allowed_network.error_number,
        denied_network.phase,
        denied_network.error_number,
        sentinel_query_errno,
        sentinel_signal_errno,
        spawn_error,
        spawned_status);
    fflush(stdout);
    char release_marker;
    while (read(STDIN_FILENO, &release_marker, 1) < 0 && errno == EINTR) {
    }
    return 0;
}
