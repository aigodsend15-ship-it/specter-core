FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json ./
COPY server.mjs ./
COPY src ./src
COPY public ./public
RUN mkdir -p /data
ENV PORT=7860 MAX_PLAYERS=128 DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 7860
CMD ["node", "server.mjs"]
