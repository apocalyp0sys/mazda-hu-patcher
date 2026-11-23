FROM debian:bookworm-slim

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        python3 \
        e2fsprogs \
        diffutils \
        gawk \
        git \
        binutils-aarch64-linux-gnu \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /work
