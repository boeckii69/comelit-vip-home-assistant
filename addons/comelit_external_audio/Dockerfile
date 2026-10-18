FROM alpine:3.22
RUN apk add --no-cache asterisk python3 ca-certificates
COPY gateway.py /opt/comelit/gateway.py
CMD ["python3", "-u", "/opt/comelit/gateway.py"]
