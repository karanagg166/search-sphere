FROM node:20-alpine

WORKDIR /app

# Install dependencies needed for node-gyp if necessary
RUN apk add --no-cache libc6-compat

# Install pnpm via corepack
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@latest --activate

# Copy dependency manifests
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./

# Install dependencies using pnpm
RUN pnpm install

# Copy application source
COPY . .

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["pnpm", "dev"]
