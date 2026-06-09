FROM node:22-slim

WORKDIR /app/frontend
COPY frontend/package.json /app/frontend/package.json
RUN npm install
COPY frontend /app/frontend
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
