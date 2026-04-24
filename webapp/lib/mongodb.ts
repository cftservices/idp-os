import { MongoClient, Db } from 'mongodb';

let client: MongoClient | null = null;

export async function getDatabase(): Promise<Db> {
  if (!client) {
    const uri = process.env.MONGODB_URI;
    if (!uri) throw new Error('MONGODB_URI not set');
    client = new MongoClient(uri);
    await client.connect();
  }
  return client.db(process.env.MONGODB_DATABASE || 'idp');
}
