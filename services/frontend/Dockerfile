FROM node:22-slim
WORKDIR /srv
COPY package.json .
RUN npm install
COPY . .
CMD ["sh", "-c", "npm run dev -- --host 0.0.0.0 --port ${PORT}"]
