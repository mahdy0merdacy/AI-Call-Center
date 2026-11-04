import { networkFailureHandler } from '../network';
import { documentsHandlers } from './documents';
import { googleHandlers } from './google';
import { loginHandlers } from './login';
import { mfaSetupHandlers } from './mfaSetup';
import { passwordResetHandlers } from './passwordReset';
import { profileHandlers } from './profile';
import { registrationHandlers } from './registration';
import { sessionHandlers } from './session';

export const handlers = [
  // First: the devtools "network down" switch short-circuits everything.
  networkFailureHandler,
  ...sessionHandlers,
  ...loginHandlers,
  ...registrationHandlers,
  ...passwordResetHandlers,
  ...googleHandlers,
  ...mfaSetupHandlers,
  ...profileHandlers,
  ...documentsHandlers,
];
