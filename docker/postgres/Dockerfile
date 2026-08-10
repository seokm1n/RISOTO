ARG POSTGRES_VERSION=18.4-bookworm

FROM postgres:${POSTGRES_VERSION} AS pgvector-builder

ARG PG_MAJOR=18
ARG PGVECTOR_VERSION=0.8.6

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        ca-certificates \
        curl \
        postgresql-server-dev-${PG_MAJOR} \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error \
        "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" \
        --output /tmp/pgvector.tar.gz \
    && mkdir -p /tmp/pgvector \
    && tar --extract --gzip --file /tmp/pgvector.tar.gz \
        --directory /tmp/pgvector --strip-components=1 \
    && make --directory /tmp/pgvector -j"$(nproc)" OPTFLAGS="" \
    && make --directory /tmp/pgvector install DESTDIR=/tmp/pgvector-install

FROM postgres:${POSTGRES_VERSION}

ARG PGVECTOR_VERSION=0.8.6

LABEL org.opencontainers.image.title="RISOTO PostgreSQL"
LABEL org.opencontainers.image.description="PostgreSQL 18.4 with pgvector"
LABEL org.opencontainers.image.version="18.4-pgvector${PGVECTOR_VERSION}"

COPY --from=pgvector-builder /tmp/pgvector-install/ /
