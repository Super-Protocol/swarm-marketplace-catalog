// Uploads a file into the application the way the composer's paperclip does:
// a graphql-upload multipart POST, returning the token a message attaches.
const fs = require('fs');
const path = require('path');

const MUTATION = `mutation ($file: Upload!, $type: FileType!) {
  uploadFile(file: $file, type: $type) { id token filename mimeType size fileType }
}`;

const MIME = {
  '.pdf': 'application/pdf',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.txt': 'text/plain',
};

async function uploadFile({ base, cookie, file, type = 'TEMPORARY' }) {
  const name = path.basename(file);
  const body = new FormData();
  body.append('operations', JSON.stringify({ query: MUTATION, variables: { file: null, type } }));
  body.append('map', JSON.stringify({ 0: ['variables.file'] }));
  body.append('0', new Blob([fs.readFileSync(file)], { type: MIME[path.extname(name)] || 'application/octet-stream' }), name);

  const response = await fetch(`${base}/graphql`, {
    method: 'POST',
    headers: { Cookie: cookie, 'apollo-require-preflight': 'true', 'x-apollo-operation-name': 'UploadFile' },
    body,
  });
  const text = await response.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error(`upload ${response.status}: ${text.slice(0, 400)}`); }
  if (parsed.errors) throw new Error(`upload errors: ${JSON.stringify(parsed.errors).slice(0, 500)}`);
  return parsed.data.uploadFile;
}

module.exports = { uploadFile };
