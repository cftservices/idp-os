import { NextResponse } from 'next/server';
import { getDatabase } from '@/lib/mongodb';

const KEY_TOPICS = [
  'idp/plc01/temperature', 'idp/plc01/Temperature',
  'idp/plc01/pressure',    'idp/plc01/Pressure',
  'idp/plc01/flow',        'idp/plc01/Flow',
  'idp/plc02/motor1_rpm',
  'idp/plc02/power_kw',
  'idp/plc03/production_rate',
];

export async function GET() {
  try {
    const db = await getDatabase();
    const collection = db.collection('plc_data');

    const docs = await collection.aggregate([
      { $match: { $or: [{ Topic: { $in: KEY_TOPICS } }, { topic: { $in: KEY_TOPICS } }] } },
      { $addFields: {
        _topic:   { $ifNull: ['$Topic',   '$topic'] },
        _payload: { $ifNull: ['$Payload', '$payload'] },
        _time:    { $ifNull: ['$Time',    '$time'] },
      }},
      { $sort: { _time: -1 } },
      { $group: {
        _id: '$_topic',
        readings: { $push: { v: '$_payload', t: '$_time' } },
      }},
      { $project: {
        readings: { $slice: ['$readings', 20] },
      }},
    ]).toArray();

    const result: Record<string, number[]> = {};
    for (const doc of docs) {
      const key = (doc._id as string).toLowerCase();
      result[key] = (doc.readings as { v: string }[])
        .map(r => parseFloat(r.v))
        .filter(n => !isNaN(n))
        .reverse();
    }

    return NextResponse.json(result);
  } catch (err) {
    console.error('History query failed:', err);
    return NextResponse.json({}, { status: 500 });
  }
}
