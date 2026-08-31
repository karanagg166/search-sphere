FROM node:20-alpine

WORKDIR /app

# Install dependencies needed for node-gyp if necessary
RUN apk add --no-cache libc6-compat

# Copy dependency manifests
COPY package.json ./

# Install npm dependencies
RUN npm install

# Copy application source
COPY . .

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["npm", "run", "dev"]
