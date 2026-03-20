const GuacamoleLite = require('guacamole-lite');

const GUACLITE_PORT = parseInt(process.env.GUACLITE_PORT || '8080', 10);
const GUACD_HOST = process.env.GUACD_HOST || 'localhost';
const GUACD_PORT = parseInt(process.env.GUACD_PORT || '4822', 10);
const SECRET_KEY = process.env.GUACLITE_SECRET_KEY || '4BQXC6JAPXst3EDAHhjpJRa2bNGi3lON';

const guacServer = new GuacamoleLite(
    { port: GUACLITE_PORT },
    { host: GUACD_HOST, port: GUACD_PORT },
    {
        maxInactivityTime: 120000,
        log: {
            level: 'ERRORS',
            stdLog: console.log,
            errorLog: console.error,
        },
        crypt: {
            cypher: 'AES-256-CBC',
            key: SECRET_KEY,
        },
        connectionDefaultSettings: {
            rdp: {
                port: '3389',
                width: 1920,
                height: 1080,
                dpi: 96,
                audio: ['audio/L16'],
                video: null,
                image: ['image/jpeg', 'image/webp', 'image/png'],
            },
            ssh: {
                port: '22',
                width: 1024,
                height: 768,
                dpi: 96,
                audio: null,
                video: null,
                image: ['image/png'],
            },
        },
    }
);

console.log(`guacamole-lite listening on :${GUACLITE_PORT}, guacd at ${GUACD_HOST}:${GUACD_PORT}`);
