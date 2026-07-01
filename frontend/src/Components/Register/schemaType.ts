import * as zod from 'zod'
import type { Schema } from './register.schema';


export type RegisterBodyType = zod.infer <typeof Schema>;