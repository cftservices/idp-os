import { NextResponse } from 'next/server';
import { getDatabase } from '@/lib/mongodb';

const TOPICS = [
  'idp/plc01/temperature', 'idp/plc01/pressure', 'idp/plc01/flow', 'idp/plc01/alarm',
  'idp/plc01/Temperature', 'idp/plc01/Pressure', 'idp/plc01/Flow', 'idp/plc01/Alarm',
  'idp/plc02/motor1_rpm', 'idp/plc02/motor2_rpm', 'idp/plc02/motor3_rpm',
  'idp/plc02/power_kw', 'idp/plc02/fault_bits',
  'idp/plc03/batch_counter', 'idp/plc03/recipe_id', 'idp/plc03/phase', 'idp/plc03/production_rate',
];

// MonsterMQ stores archived messages in the Archive group collection.
// Field names are PascalCase (Kotlin/Vert.x convention): Topic, Payload, Time.
// If your MonsterMQ version uses lowercase, change Topic→topic, Payload→payload, Time→time.
export async function GET() {
  try {
    const db = await getDatabase();
    const collection = db.collection('plc_data');

    const docs = await collection.aggregate([
      { $match: { $or: [{ Topic: { $in: TOPICS } }, { topic: { $in: TOPICS } }] } },
      { $addFields: {
        _topic: { $ifNull: ['$Topic', '$topic'] },
        _payload: { $ifNull: ['$Payload', '$payload'] },
        _time: { $ifNull: ['$Time', '$time'] },
      }},
      { $sort: { _time: -1 } },
      { $group: {
        _id: '$_topic',
        value: { $first: '$_payload' },
        time: { $first: '$_time' },
      }},
    ]).toArray();

    const result: Record<string, { value: string; time: unknown }> = {};
    for (const doc of docs) {
      // Normalize topic to lowercase for the frontend
      const key = (doc._id as string).toLowerCase();
      result[key] = { value: String(doc.value), time: doc.time };
    }

    return NextResponse.json(result);
  } catch (err) {
    console.error('MongoDB query failed:', err);
    return NextResponse.json({}, { status: 500 });
  }
}
