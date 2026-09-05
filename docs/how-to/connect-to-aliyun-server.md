# Connect to the Aliyun deploy server

The single ECS box we deploy to. SSH key-based login only — password auth is
disabled on the server, so there is no password to leak or rotate here.

## Facts

| | |
|---|---|
| Public IP | `8.140.221.53` (EIP) |
| Private IP | `172.23.37.121` (Aliyun VPC — unreachable except from inside the VPC) |
| Instance ID | `i-2ze279ovmlvp4zgqwqyt` |
| OS | Ubuntu 26.04 LTS |
| Login user | `root` |
| Auth | SSH public key only (`PasswordAuthentication no`) |
| Spec | 2 vCPU, 1.6 GiB RAM, 40 GB disk |

Low RAM: add swap before running multi-container Docker or heavy builds.

## Connect

1. Have the private key on your machine. The operator key lives at
   `~/.ssh/aliyun_8140` (ed25519). It is **not** in this repo — never commit a
   private key.

       ssh -i ~/.ssh/aliyun_8140 root@8.140.221.53

   Criterion: you land on `root@iZmlvp4zgqwqytZ:~#`.

2. If you see `Permission denied (publickey)`, your key is not in the server's
   `~/.ssh/authorized_keys`. See "Authorize a new key" below.

3. If it hangs / times out, the Aliyun security group is not allowing your
   source IP on TCP 22. Add your public egress IP (`curl -s ifconfig.me`) to the
   security group inbound rules in the Aliyun console.

## Authorize a new key

From a machine that is already in, or via the Aliyun console VNC:

    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    echo 'ssh-ed25519 AAAA... user@host' >> ~/.ssh/authorized_keys   # the new public key
    chmod 600 ~/.ssh/authorized_keys

Criterion: `ssh -i PATH_TO_MATCHING_PRIVATE_KEY root@8.140.221.53` succeeds.

## Convenience: SSH config alias

Append to `~/.ssh/config` so `ssh aliyun` just works:

    Host aliyun
        HostName 8.140.221.53
        User root
        IdentityFile ~/.ssh/aliyun_8140
        IdentitiesOnly yes
