import {
  Admin,
  Resource,
  ListGuesser,
  EditGuesser,
  ShowGuesser,
} from "react-admin";
import { Layout } from "./Layout";
import simpleRestProvider from 'ra-data-simple-rest';

const dataProvider = simpleRestProvider('http://localhost:8000');

export const App = () => (
  <Admin layout={Layout} dataProvider={dataProvider}>
    <Resource name="customers" list={ListGuesser} />
  </Admin>
);
