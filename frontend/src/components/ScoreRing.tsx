'use client';

// Animated score ring — adapted from TalentHive's ScoreRing concept

import { motion } from 'framer-motion';
import { scoreColor } from '@/lib/utils';

export default function ScoreRing({ score, size = 64 }: { score: number; size?: number }) {
  const stroke = size >= 60 ? 6 : 4;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="rgba(120,120,140,0.2)"
          strokeWidth={stroke}
          fill="none"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="currentColor"
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          className={scoreColor(score)}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: 'easeOut' }}
          strokeDasharray={circumference}
        />
      </svg>
      <span className={`absolute text-sm font-semibold ${scoreColor(score)}`}>{score}</span>
    </div>
  );
}
